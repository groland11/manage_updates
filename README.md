![last commit](https://img.shields.io/github/last-commit/groland11/manage_updates.svg)
![languages](https://img.shields.io/github/languages/top/groland11/manage_updates.svg)
![license](https://img.shields.io/github/license/groland11/manage_updates.svg)

# manage_updates
Manage YAML configuration files for operating system updates via Puppet
- Edit YAML configuration file to switch updates on or off
- Extended logging

## Prerequisites
- Python >= 3.6
- Red Hat Enterprise Linux >= 7

## Usage
```
./manage-updates.py -h
usage: manage-updates.py [on|off|status] [-h] [-v] [-d]

Switch updates on/off

optional arguments:
-h, --help            show this help message and exit
-v, --verbose         enable verbose output
-d, --debug           generate additional debugging output
```
## Examples
