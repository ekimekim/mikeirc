import gevent.monkey
gevent.monkey.patch_all()

from geventirc.irc import Client
import geventirc.handlers as handlers
from geventirc.message import Join

import sys
from getpass import getpass


host = 'irc.desertbus.org'
port = 6667
nick = 'ekimekim'
real_name = 'ekimekim'
channels = ['#desertbus']

NICK_HIGHLIGHT = "31;1"
CHAN_WIDTH = 12
USER_WIDTH = 12


def main(*args):

	client = Client(host, nick, port, real_name=real_name)

	password = getpass("Password for {}: ".format(nick))
	client.add_handler(RespectfulNickServHandler(nick, password))

	for channel in channels:
		client.add_handler(handlers.JoinHandler(channel))

	client.add_handler(generic_recv)

	client.start()
	gevent.spawn(in_worker)

	client.join()


class IdentifiedJoinHandler(object):
	commands = ['NOTICE']
	def __init__(self, channels):
		self.channels = channels
	def __call__(self, client, msg):
		if msg.sender == 'NickServ' and msg.params and ' '.join(msg.params[1:]).startswith("You are now identified"):
			for chan in self.channels:
				client.send_message(Join(chan))


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
		speaker = msg.params[0]
		text = ' '.join(msg.params[1:])
		out("({msg.sender:SENDER_WIDTH}) {speaker:USER_WIDTH}: {text}".format(
			msg=msg, speaker=speaker, text=text, **globals()
		))
	else:
		try:
			n = int(msg.command)
		except ValueError:
			pass
		# numeric command - print as is
		out(msg.encode().rstrip())


def out(s):
	# highlight nick
	if NICK_HIGHLIGHT:
		s = s.replace(nick, "\x1b[{highlight}m{nick}\x1b[m".format(nick=nick, highlight=NICK_HIGHLIGHT))
	print s
	print


def in_worker():
	fd = sys.stdin.fileno()
	def read(n):
		# the n will always be 1
		r,w,x = select([fd], [], [])
		assert fd in r
		return fd.read(1)
	with editing.get_termattrs(fd):
		while True:
			line = editing.readline()
			if line:
				pass
				# TODO

if __name__=='__main__':
	sys.exit(main(*sys.argv) or 0)
