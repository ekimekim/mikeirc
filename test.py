import gevent
import gevent.select
import itertools as it

from withtermios import TermAttrs
from editing import readline, get_termattrs, HiddenCursor
import escapes

import sys

def input_fn():
	r, w, x = gevent.select.select([sys.stdin], [], [])
	assert sys.stdin in r
	return sys.stdin.read(1)

def input_loop():
	with get_termattrs(), HiddenCursor:
		x = ''
		try:
			while x != 'exit':
				x = readline(input_fn=input_fn)
				print repr(x)
		except EOFError:
			pass

def spam():
	for n in it.count():
		gevent.sleep(1)
		print escapes.CLEAR_LINE + "Blah blah %d" % n

gevent.spawn(spam)
input_loop()
print
