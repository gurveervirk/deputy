urepath = ''
for basis in ( 'basis-link', 'basis', '' ):
for ure in ( 'ure-link', 'ure', 'URE', '' ):
if os.path.isfile(realpath(basepath, basis, ure, 'lib', 'unorc')):
urepath = realpath(basepath, basis, ure)
info(3, "Found %s in %s" % ('unorc', realpath(urepath, 'lib')))
# Break the inner loop...
break
# Continue if the inner loop wasn't broken.
else:
continue
# Inner loop was broken, break the outer.
break
