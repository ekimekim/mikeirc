import gevent.monkey
gevent.monkey.patch_all()

from geventirc.irc import Client
import geventirc.handlers as handlers
from geventirc.message import Join

import sys
import os
from getpass import getpass
import socket

from gevent.select import select
from gevent.pool import Group
from gevent.backdoor import BackdoorServer

import editing


host = 'irc.desertbus.org'
port = 6667
nick = 'ekimekim'
real_name = 'ekimekim'
channel = '#desertbus'

NICK_HIGHLIGHT = "31;1"
PRIVATE_HIGHLIGHT = "1"
COMMAND_HIGHLIGHT = "30"
KICK_HIGHLIGHT = "35"

SENDER_WIDTH = 12
USER_WIDTH = 12

USER_HIGHLIGHTS = {
	'BidServ': '1;36',
}
KEYWORD_HIGHLIGHTS = {
	nick: NICK_HIGHLIGHT, # original nick always gets highlighted
}

EXCLUDE_NUMERICS = {5}

main_greenlet = None


class ConnClosed(Exception):
	def __str__(self):
		return "Connection Closed"


def main(*args):
	global main_greenlet

	password = getpass("Password for {}: ".format(nick))

	workers = Group()
	main_greenlet = gevent.getcurrent()

	backdoor = BackdoorServer(('localhost',1666))
	backdoor.start()

	while True:
		try:
			try:
				client = Client(host, nick, port, real_name=real_name, disconnect_handler=on_disconnect)

				client.add_handler(handlers.ping_handler, 'PING')
				client.add_handler(RespectfulNickServHandler(nick, password))
				client.add_handler(handlers.JoinHandler(channel))
				client.add_handler(generic_recv)

				client.start()
				#workers.spawn(in_worker)

				client.join()
			except BaseException:
				client.stop()
				workers.kill()
				raise
		except (socket.error, ConnClosed), ex:
			print ex
			gevent.sleep(1)


def on_disconnect(client):
	gevent.kill(main_greenlet, ConnClosed())
	raise gevent.GreenletExit()


class RespectfulNickServHandler(handlers.NickServHandler):
	commands = handlers.NickServHandler.commands + ['NICK']
	def __call__(self, client, msg):
		global nick
		if msg.command == 'NICK':
			try:
				target, arg = msg.params
				if target == nick:
					self.nick = msg.params[-1]
					out("Warning: Server forced nick change to {!r}".format(nick))
			except ValueError:
				pass
		else:
			super(RespectfulNickServHandler, self).__call__(client, msg)
		nick = self.nick


def generic_recv(client, msg):

	target = msg.params[0]
	text = ' '.join(msg.params[1:])
	sender = msg.sender
	is_action = False

	highlight = lambda outstr, sequence: '\x1b[{}m{}\x1b[m'.format(sequence, outstr)

	# default outstr
	outstr = highlight("{sender:>{SENDER_WIDTH}}: {target} {msg.command} {text}", COMMAND_HIGHLIGHT)

	if msg.command == 'PRIVMSG':
		if not msg.params:
			# bad message
			out(msg.encode().rstrip())
			return

		for param in msg.ctcp_params:
			if param and param[0] == 'ACTION':
				is_action = True
				text = param[1]

		if target == channel:
			if is_action:
				outstr = "{sender:>{SENDER_WIDTH}} {text}"
			else:
				outstr = "{sender:>{SENDER_WIDTH}}: {text}"
			if sender in USER_HIGHLIGHTS:
				outstr = highlight(outstr, USER_HIGHLIGHTS[sender])
		else:
			# private message
			sender = "[{}]".format(sender)
			if target != nick:
				text = '[{}] {}'.format(target, text)
			outstr = highlight("{sender:>{SENDER_WIDTH}}: {text}", PRIVATE_HIGHLIGHT)
	elif msg.command == 'QUIT':
		outstr = highlight("{sender:>{SENDER_WIDTH}} quits: {text}", COMMAND_HIGHLIGHT)
	elif msg.command == 'NICK':
		outstr = highlight("{sender:>{SENDER_WIDTH}} changes their name to {target}", COMMAND_HIGHLIGHT)
	elif msg.command == 'KICK':
		empty = ''
		target, text = text.split(' ', 1)
		outstr = highlight("{empty:>{SENDER_WIDTH}} {target} kicked by {sender}: {text}", KICK_HIGHLIGHT)
	elif msg.command == 'PING':
		return
	else:
		try:
			n = int(msg.command, 10)
		except ValueError:
			# unknown message type
			pass
		else:
			# numeric command - unless excluded, print
			if n in EXCLUDE_NUMERICS: return
			if sender == host and target == nick:
				outstr = highlight("{msg.command:>{SENDER_WIDTH}}: {text}", COMMAND_HIGHLIGHT)
			else:
				# not sure what circumstances this would apply for, use default
				pass
	d = globals().copy()
	d.update(locals())
	out(outstr.format(**d))


def out(s):
	# highlight nick
	keywords = KEYWORD_HIGHLIGHTS.copy()
	keywords.update({nick: NICK_HIGHLIGHT})
	for keyword, highlight in keywords.items():
		s = s.replace(keyword, "\x1b[{highlight}m{keyword}\x1b[m".format(keyword=keyword, highlight=highlight))
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
