
import re

import mesa_namelists as mesa


MESA_DIR = '/home/asimazbunzel/Developments/mesa-r15140'
defaults = mesa.MESAdefaults(mesa_dir=MESA_DIR)

# get keys of every single control in MESA
attrs = vars(defaults)

namelist_keys = [key for key in attrs['groups'].keys()]

keys = []
for nkey in namelist_keys:
    keys.extend(( attrs['groups'][nkey].keys() ))

with open('controls_vim.txt', 'w') as f:
    for key in keys:

        if "(" in key:
            print(key)
            key = re.sub("\([0-9]\)", "", key)
            print(key)

        string=f"syntax match inlistKeyword /\zs{key}\ze\s*/\n"
        f.write(string)
