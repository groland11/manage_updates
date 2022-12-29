#!/usr/bin/env python3
""" Switch updates on/off by writing Puppet configuration files

Requirements
    Python >= 3.6
    Packages: python3-fasteners, python3-pyyaml

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
You should have received a copy of the GNU General Public License along with
this program. If not, see <http://www.gnu.org/licenses/>.
"""
import argparse
import fasteners
import logging
import os
import re
import sys
import yaml

from configparser import ConfigParser,MissingSectionHeaderError
from datetime import date, datetime, timedelta
from enum import Enum
from subprocess import run, TimeoutExpired, PIPE
from typing import Optional

__license__ = "GPLv3"
__version__ = "0.1"


# Global defaults that can be changed by command line parameters
DEBUG = False
CONFIG_FILE = "/usr/local/etc/updates.conf"
LOG_FILE = "/var/log/scripts/updates.log"
YAML_DIR = "/appl/puppet/enc"


# Update modes that can be set via command line parameters
class Mode(Enum):
    ON = 'on'
    OFF = 'off'
    UPDATE = 'update'
    STATUS = 'status'

    @classmethod
    def has_value(this, value):
        return value in [member.value for member in Mode]


# Global logging object
logger = logging.getLogger(__name__)


def parseargs() -> argparse.Namespace:
    """ Parse command-line arguments """
    parser = argparse.ArgumentParser(description='Switch updates on/off')
    parser.add_argument(
        '-q', '--quiet', required=False,
        help='quiet mode, do not print statistics', dest='quiet',
        action='store_true')
    parser.add_argument(
        '-d', '--debug', required=False,
        help='enable debug output', dest='debug',
        action='store_true')
    parser.add_argument(
        '-c', '--config', required=False,
        help='configuration file', dest='config_file',
        default=CONFIG_FILE, action='store')
    parser.add_argument(
        '-y', '--yamldir', required=False,
        help='directory with YAML puppet files', dest='yaml_dir',
        default=YAML_DIR, action='store')
    parser.add_argument(
        '-l', '--logfile', required=False,
        help='path to logfile', dest='logfile',
        default=LOG_FILE, action='store')
    parser.add_argument('-V', '--version', action='version', version='%(prog)s ' + __version__)

    # Subcommand "mode" can be one of: "on", "off", "status"
    sp = parser.add_subparsers(dest="mode")
    mode_sp = sp.add_parser(Mode.ON.value)
    mode_sp = sp.add_parser(Mode.OFF.value)
    mode_sp = sp.add_parser(Mode.UPDATE.value)
    mode_sp = sp.add_parser(Mode.STATUS.value)

    args = parser.parse_args()
    return args


class LogFilterWarning(logging.Filter):
    """Logging filter >= WARNING"""
    def filter(self, record):
        return record.levelno in (logging.DEBUG, logging.INFO, logging.WARNING)


def get_logger(logfile: Optional[str], debug: bool = False) -> logging.Logger:
    """Retrieve logging object"""
    if debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    # Log everything >= DEBUG to stdout
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    h1.setFormatter(logging.Formatter(fmt='%(asctime)s [%(process)d] %(levelname)s: %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S'))
    h1.addFilter(LogFilterWarning())

    # Log errors to stderr
    h2 = logging.StreamHandler(sys.stderr)
    h2.setFormatter(logging.Formatter(fmt='%(asctime)s [%(process)d] %(levelname)s: %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S'))
    h2.setLevel(logging.ERROR)

    logger.addHandler(h1)
    logger.addHandler(h2)

    # Log everything >= DEBUG to logfile
    try:
        h3 = logging.FileHandler(logfile, encoding="utf-8")
        h3.setLevel(logging.DEBUG)
        h3.setFormatter(logging.Formatter(fmt='%(asctime)s [%(process)d] %(levelname)s: %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S'))
        logger.addHandler(h3)
    except FileNotFoundError as e:
        logger.error(f"Invalid directory or logfile ({e})")

    return logger


class Updates:
    def __init__(self, mode: Mode=Mode.STATUS, yaml_dir: str="", config_file: str=""):
        self._mode: Mode = mode
        self._yaml_dir:str = yaml_dir
        self._config_file:str = config_file
        self._yaml_files: dict = {}
        self._downtimes: list = []
        self._current_downtime:str = None

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, new_mode: Mode):
        if new_mode in Mode:
            self._mode = new_mode
        else:
            raise ValueError(f"Unsupported new mode {new_mode}")

    @property
    def downtimes(self):
        return self._downtimes

    @property
    def current_downtime(self):
        return self._current_downtime

    def read_config(self):
        """Read current configuration"""

        # Read local configuration file
        downtimes_config = []
        config_object = ConfigParser()
        config_object.read(self._config_file)
        try:
            userinfo = config_object["MAIN"]
            logger.debug(f"Reading config file {self._config_file}")
            downtimes_config += [s.strip() for s in userinfo["downtime"].split(",")]
        except KeyError as e:
            pass
        except MissingSectionHeaderError as e:
            logger.error(f"Invalid configuration file format ({self._config_file})")
        except:
            logger.error(f"Unable to parse configuration file {self._config_file}")

        if len(downtimes_config) > 0:
            self._downtimes.extend(downtimes_config)
            logger.debug(f"Downtimes: {self._downtimes}")

        # Read puppet configuration files
        try:
            for f in os.listdir(self._yaml_dir):
                if f.endswith(".yaml"):
                    logger.debug(f"YAML file {f}")
                    self._yaml_files[f] = None
        except FileNotFoundError as e:
            logger.error(f"Invalid directory {self._yaml_dir} ({e})")
            raise(e)

        for f in self._yaml_files:
            with open(os.path.join(self._yaml_dir, f)) as yaml_file:
                try:
                    data = yaml.load(yaml_file, Loader=yaml.loader.SafeLoader)
                    self._yaml_files[f] = data
                    #print(yaml.dump(sw, indent=4, default_flow_style=False))
                except yaml.parser.ParserError as e:
                    logger.error(f"Invalid YAML file {f} ({e})")

    def statistics(self, quiet: bool=False):
        """Print overall statistics"""
        stats = {"security": 0, "security_off": 0, "none": 0}

        for f, data in dict(sorted(self._yaml_files.items())).items():
            try:
                update_mode = data['properties']['updates']
                logger.debug(f"{f}: updates = {update_mode}")
                logger.info(f"{f.replace('.yaml', '')} - updates: {update_mode}")
            except KeyError as e:
                logger.debug(f"No updates for {f}")
                
            counter = stats.get(update_mode)
            if counter:
                stats[update_mode] +=1
            else:
                stats[update_mode] = 1

        if not quiet:
            for key, value in stats.items():
                if key.strip() == "none":
                    msg = "no updates"
                elif key.strip() == "security":
                    msg = "security updates ON"
                elif key.strip() == "security_off":
                    msg = "security updates OFF"
                else:
                    msg ="unknown updates status"
                logger.info(f"Hosts with {msg:>20}: {value:>3}")

    def write_config(self):
        """Write new configuration to YAML files"""
        logger.debug(f"Write new mode: {self._mode}")
        downtime = False

        if self.check_downtime():
            logger.info(f"Downtime detected in config file: {self.current_downtime}")
            downtime = True
            if self._mode == Mode.ON:
                logger.warning(f"Aborting because updates cannot be enabled in a downtime.")
                return

        for f, data in self._yaml_files.items():
            old_mode = data['properties']['updates']
            new_mode = ""

            # Switching updates on
            if self._mode == Mode.ON:
                new_mode = "security"

            # Switching updates off
            if self._mode == Mode.OFF:
                new_mode = "none"

            # Temporarily disabling updates in downtime and switching back after downtime
            if self._mode == Mode.UPDATE:
                if downtime:
                    if old_mode == "security":
                        new_mode = "security_off"
                else:
                    if old_mode == "security_off":
                        new_mode = "security"

            # Writing new mode to YAML file
            if new_mode != "" and old_mode != "":
                data['properties']['updates'] = new_mode
                with open(os.path.join(self._yaml_dir, f), 'w') as yaml_file:
                    if not DEBUG:
                        data1 = yaml.dump(data, yaml_file)
                    logger.debug(f"{f}: updates = {new_mode}")

    def check_downtime(self) -> bool:
        """ Check if downtime is configured for today

        Throws ValueError exception if downtimes have wrong format
        """

        today = date.today()
        
        for downtime in self._downtimes:
            years_missing: bool = False

            minstring, maxstring = downtime.split("-")
            if maxstring == "":
                maxstring = minstring

            # Start date of downtime
            day, month, year = minstring.strip().split(".")
            if year == "":
                years_missing = True
                year = str(today.year)
            mindate = date(int(year.strip()), int(month.strip()), int(day.strip()))

            # End date of downtime
            day, month, year = maxstring.strip().split(".")
            if year == "":
                if years_missing:
                    year = str(today.year)
                else:
                    # Inconsistent use of years
                    logger.error(f"Inconsistent use of year in configuration downtimes")
                    raise ValueError(f"Inconsistent use of year in configuration downtimes")
            else:
                if years_missing:
                    # Inconsistent use of years
                    loggger.error(f"Inconsistent use of year in configuration downtimes")
                    raise ValueError(f"Inconsistent use of year in configuration downtimes")
            maxdate = date(int(year.strip()), int(month.strip()), int(day.strip()))

            # Consistency checks
            if maxdate < mindate:
                if years_missing:
                    # Is downtime without year and spanning over two years?
                    year = str(today.year + 1)
                    maxdate = date(int(year.strip()), int(month.strip()), int(day.strip()))
                else:
                    raise ValueError(f"End of downtime {maxstring} earlier thant start of downtime {minstring}")

            # Are we currently in a downtime?
            if today <= maxdate and today >= mindate:
                self._current_downtime = downtime.strip()
                return True

        return False


def main():
    """Main program flow"""
    ret = 0 # Return code of script
    lockfile = "/var/run/updates.lock" # Lockfile to prevent multiple instances running simultaneously

    # Set up environment
    args = parseargs()
    get_logger(args.logfile, args.debug)

    if args.debug:
        DEBUG = True

    # Ensure that only one script runs at a time
    lock = fasteners.InterProcessLock(lockfile)
    if not lock.acquire(timeout=1):
        logger.error(f"Script is already running (s. {lockfile})")
        exit(2)

    # Determine mode from command line
    try:
        mode = Mode(args.mode)
    except ValueError:
        mode = None
    else:
        logger.info(f"Running as user {os.getlogin()}: {mode.value}")

    updates = Updates(mode, args.yaml_dir, args.config_file)
    # Read configuration files
    try:
        updates.read_config()
    except Exception as e:
        logger.error(f"Invalid configuration file ({e})")
        ret = 3
    else:
        # Mode switch
        try:
            # Mode "status"
            if mode == Mode.STATUS:
                updates.statistics(quiet=args.quiet)
            # Mode "on" / "off" / "update"
            elif mode:
                updates.write_config()
                updates.statistics(quiet=False)
        except ValueError as e:
            logger.error(f"Invalid configuration of downtimes {updates.downtimes}")
            logger.debug(f"{e}")
            ret = 2
        except Exception as e:
            logger.debug(f"Error: {e} at line {e.__traceback__.tb_lineno}")
            ret = 1

    lock.release()
    exit(ret)


if __name__ == '__main__':
    main()
