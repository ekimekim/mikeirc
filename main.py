import gevent.monkey
gevent.monkey.patch_all()

from geventirc.irc import Client
import geventirc.handlers as handlers
from geventirc.message import Join

import sys
import os
from getpass import getpass

from gevent.select import select
from gevent.pool import Group

import editing


host = 'irc.desertbus.org'
port = 6667
nick = 'ekimekim'
real_name = 'ekimekim'
channel = '#desertbus'

NICK_HIGHLIGHT = "31;1"
PRIVATE_HIGHLIGHT = "1"
SENDER_WIDTH = 12
USER_WIDTH = 12

EXCLUDE_NUMERICS = {5}


def main(*args):

	workers = Group()

	try:
		client = Client(host, nick, port, real_name=real_name)

		password = getpass("Password for {}: ".format(nick))
		client.add_handler(RespectfulNickServHandler(nick, password))

		client.add_handler(handlers.JoinHandler(channel))

		client.add_handler(generic_recv)

		client.start()
		workers.spawn(in_worker)

		client.join()
	except BaseException:
		workers.kill()
		raise


class RespectfulNickServHandler(handlers.NickServHandler):
	commands = handlers.NickServHandler.commands + ['NICK']
	def __call__(self, client, msg):
		global nick
		if msg.command == 'NICK':
			if msg.params:
				self.nick = msg.params[-1]
				out("Warning: Server forced nick change to {!r}".format(nick))
		else:
			super(RespectfulNickServHandler, self).__call__(client, msg)
		nick = self.nick


def generic_recv(client, msg):
	if msg.command == 'PRIVMSG':
		if not msg.params:
			out(msg.encode().rstrip())
			return

		target = msg.params[0]
		text = ' '.join(msg.params[1:])
		sender = msg.sender
		is_action = False
		for param in msg.ctcp_params:
			if param and param[0] == 'ACTION':
				is_action = True
				text = param[1]

		if target == channel:
			if is_action:
				outstr = "{sender:>{SENDER_WIDTH}} {text}"
			else:
				outstr = "{sender:>{SENDER_WIDTH}}: {text}"
		else:
			# private message
			sender = "[{}]".format(sender)
			if target != nick:
				text = '[{}] {}'.format(target, text)
			outstr = "\x1b[{PRIVATE_HIGHLIGHT}m{sender:>{SENDER_WIDTH}}\x1b[m: {text}"
		d = globals().copy()
		d.update(locals())
		out(outstr.format(**d))
	else:
		try:
			n = int(msg.command, 10)
		except ValueError:
			n = None
		# numeric command - print as is unless excluded
		if n not in EXCLUDE_NUMERICS:
			out(msg.encode().rstrip())


def out(s):
	# highlight nick
	if NICK_HIGHLIGHT:
		s = s.replace(nick, "\x1b[{highlight}m{nick}\x1b[m".format(nick=nick, highlight=NICK_HIGHLIGHT))
	print s


def in_worker():
	fd = sys.stdin.fileno()
	def read():
		r,w,x = select([fd], [], [])
		assert fd in r
		return os.read(fd, 1)
	with editing.get_termattrs(fd):
		while True:
			line = editing.readline(input_fn=read)
			if line == 'exit': sys.exit() # for testing
			if line:
				pass
				# TODO

if __name__=='__main__':
	sys.exit(main(*sys.argv) or 0)
