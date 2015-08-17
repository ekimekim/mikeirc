import gevent.monkey
gevent.monkey.patch_all()

import os
import random
import re
import string
import sys
import traceback
from getpass import getpass

from girc import Client
from girc.message import Privmsg, Message

import gtools
import requests
from backoff import Backoff
from gevent.select import select
from lineedit import LineEditing
from pyconfig import CONF

import irccolors
from smart_reset import smart_reset


COMMAND_HIGHLIGHT = "30"
KICK_HIGHLIGHT = "35"
PRIVATE_HIGHLIGHT = "1"
NICK_HIGHLIGHT = "31;1"
USER_HIGHLIGHT = "32"
OP_HIGHLIGHT = "33"
TWITCH_EMOTE_HIGHLIGHT = "36"

SENDER_WIDTH = 12
USER_WIDTH = 12

USER_HIGHLIGHTS = {
	'BidServ': '1;33',
	'Bidbot': '1;33',
	'DBEngineering': '1',
	'DBCommand': '1',
	'twitchnotify': '33',
}
KEYWORD_HIGHLIGHTS = {
	'ekim': NICK_HIGHLIGHT, # das me
}
REGEX_HIGHLIGHTS = {}

EXCLUDE_NUMERICS = {5}

IGNORE_NICKS = {"fbt"}

TWITCH_EVENT_SERVERS = {
	'192.16.64.143',
	'192.16.64.150',
	'192.16.71.221',
	'192.16.71.236',
	'199.9.252.54',
}

main_greenlet = None


USER_HIGHLIGHTS = {nick.lower(): highlight for nick, highlight in USER_HIGHLIGHTS.items()}


def read():
	fd = sys.stdin.fileno()
	r,w,x = select([fd], [], [])
	assert fd in r
	return os.read(fd, 1)
editor = LineEditing(input_fn=read)


def main():

	# loads from the default config file, then argv and env. setting --conf allows you
	# to specify a conf file at "argv level" priority, overriding the defaults.
	CONF.load_all(conf_file=os.path.join(os.path.dirname(__file__), '/etc/mikeirc.conf'))

	# this is horrible
	# required keys
	host = CONF['host']
	CONF['nick'] # just check it's there
	CONF['channel']
	# optional keys. note that defaults are None (which works as False)
	port = int(CONF.port) or 6667
	backdoor = CONF.backdoor
	twitch = CONF.twitch
	password = CONF.password

	# resolve password config options to actual password values
	if password is None and not CONF.no_auth:
		password = getpass("Password for {}: ".format(CONF.nick))
	if not password: # password == '' is different to password == None
		password = None
	if twitch:
		nickserv_password = None
	else:
		nickserv_password = "{} {}".format(CONF.email, password) if CONF.email else password
		password = None

	if backdoor:
		if backdoor is True:
			backdoor = 1235
		gtools.backdoor(backdoor)

	if twitch:
		# make changes to host
		if not isinstance(twitch, basestring):
			host = 'irc.twitch.tv'
		elif twitch == 'event':
			host = random.choice(TWITCH_EVENT_SERVERS)
			print 'Using twitch event server:', host
		else:
			host = twitch
			print 'Using custom twitch server:', host

		# make channel owner bold
		USER_HIGHLIGHTS[CONF.channel.lstrip('#')] = '1'

		# load emotes
		try:
			print "Loading emotes..."
			emotes = requests.get('https://api.twitch.tv/kraken/chat/emoticons').json()
			emotes = [x['regex'] for x in emotes['emoticons']]
			n = len(emotes)
			emotes = "|".join(["(?:{})".format(x.encode("utf-8")) for x in emotes])
			emotes = r"\b(?:{})\b".format(emotes)
			emotes = re.compile(emotes)
			print "{} emotes loaded".format(n)
			REGEX_HIGHLIGHTS[emotes] = TWITCH_EMOTE_HIGHLIGHT
		except Exception:
			print "Failed to load emotes:"
			traceback.print_exc()

	client = None
	backoff = Backoff(0.2, 10, 2)
	while True:
		try:
			client = Client(host, CONF.nick, port, real_name=CONF.real_name,
							password=password, nickserv_password=nickserv_password)
			channel = client.channel(CONF.channel)
			channel.join()

			client.handler(generic_recv)

			client.start()
			# spawn input greenlet in client's Group, linking its lifecycle to the client
			client._group.spawn(in_worker, client)

			backoff.reset() # successful startup
			client.wait_for_stop()
		except Exception:
			traceback.print_exc()
			time = backoff.get()
			print "retrying in %.2f seconds..." % time
			gevent.sleep(time)
		else:
			break


normalize_patterns = r"([^|]+)|[^|]*", r"([^\[]+)\[[^\]]*\]"
normalize_patterns = [re.compile("^{}$".format(pattern)) for pattern in normalize_patterns]
def nick_normalize(nick):
	"""Lowercases and looks for forms:
	NICK|STATUS
	NICK[STATUS]
	and strips the STATUS.
	"""
	nick = nick.lower()
	for pattern in normalize_patterns:
		match = pattern.match(nick)
		if match:
			nick, = match.groups()
	return nick


def generic_recv(client, msg, sender=None):

	params = msg.params
	text = ' '.join(msg.params)
	sender = sender or msg.sender
	is_action = False
	quiet = CONF.quiet

	if sender in IGNORE_NICKS:
		return

	highlight = lambda outstr, sequence: '\x1b[{}m{}\x1b[m'.format(sequence, outstr)

	# default outstr
	outstr = highlight("{sender:>{SENDER_WIDTH}}: {msg.command} {text}", COMMAND_HIGHLIGHT)

	nosend = False

	if msg.command == 'PRIVMSG':
		target, text = msg.target, msg.payload

		if not msg.params:
			# bad message
			out(client, msg.encode().rstrip())
			return

		if msg.ctcp:
			command, ctcp_arg = msg.ctcp
			if ctcp_command == 'ACTION':
				is_action = True
				text = ctcp_arg

		if target == CONF.channel:
			if is_action:
				outstr = "{sender:>{SENDER_WIDTH}} {text}"
			else:
				outstr = "{sender:>{SENDER_WIDTH}}: {text}"
			if sender.lower() in USER_HIGHLIGHTS:
				outstr = highlight(outstr, USER_HIGHLIGHTS[sender])
		else:
			# private message
			sender = "[{}]".format(sender)
			if target != client.nick:
				text = '[{}] {}'.format(target, text)
			if is_action:
				outstr = highlight("{sender:>{SENDER_WIDTH}} {text}", PRIVATE_HIGHLIGHT)
			else:
				outstr = highlight("{sender:>{SENDER_WIDTH}}: {text}", PRIVATE_HIGHLIGHT)
	elif msg.command == 'QUIT':
		outstr = highlight("{sender:>{SENDER_WIDTH}} quits: {text}", COMMAND_HIGHLIGHT)
		if quiet: nosend = True
	elif msg.command == 'NICK':
		target, text = params[0], ' '.join(params[1:])
		outstr = highlight("{sender:>{SENDER_WIDTH}} changes their name to {target}", COMMAND_HIGHLIGHT)
		if quiet: nosend = True
	elif msg.command == 'KICK':
		chan, target, text = params[0], params[1], ' '.join(params[2:])
		empty = ''
		outstr = highlight("{empty:>{SENDER_WIDTH}} {target} kicked by {sender}: {text}", KICK_HIGHLIGHT)
	elif msg.command in ('PING', 'PONG'):
		return
	else:
		if quiet: nosend = True
		try:
			n = int(msg.command, 10)
		except ValueError:
			# unknown message type
			pass
		else:
			# numeric command - unless excluded, print
			if n in EXCLUDE_NUMERICS: return
			if sender == client.hostname and params and params[0] == client.nick:
				outstr = highlight("{msg.command:>{SENDER_WIDTH}}: {text}", COMMAND_HIGHLIGHT)
			else:
				# not sure what circumstances this would apply for, use default
				pass
	if not nosend:
		kwargs = globals().copy()
		kwargs.update(locals())
		out(client, outstr.format(**kwargs))


def out(client, s):
	channel = client.channel(CONF.channel)

	# irc style characters
	s = irccolors.apply_irc_formatting(s)

	# scan for regexes
	for regex, highlight in REGEX_HIGHLIGHTS.items():
		if isinstance(regex, basestring):
			regex = re.compile(regex)
		def wrap_it(match):
			return '\x1b[{}m{}\x1b[m'.format(highlight, match.group())
		s = regex.sub(wrap_it, s)

	# highlight nick
	keywords = {}
	keywords.update({user: USER_HIGHLIGHT for user in channel.users.users})
	keywords.update({user: OP_HIGHLIGHT for user in channel.users.ops})
	keywords.update({nick_normalize(client.nick): NICK_HIGHLIGHT})
	keywords.update(KEYWORD_HIGHLIGHTS)
	keywords = {k.lower(): v for k, v in keywords.items()}

	outbuf = ''
	buf = ''
	in_escape = False
	for c in s + '\0': # add terminator to ensure final buf contents get flushed
		if c in string.letters + string.digits + '_-' and not in_escape:
			buf += c
		else:
			if buf.lower() in keywords and not in_escape:
				outbuf += '\x1b[{}m{}\x1b[m'.format(keywords[buf.lower()], buf)
			else:
				outbuf += buf
			outbuf += c
			if in_escape and c == 'm':
				in_escape = False
			buf = ''
			if outbuf.endswith('\x1b['):
				in_escape = True
	outbuf = outbuf[:-1] # remove terminator
	editor.write(smart_reset(outbuf))


def in_worker(client):
	with editor:
		try:
			while True:
				line = editor.readline()
				if isinstance(line, unicode):
					line = line.encode('utf-8')
				if line:
					cmd = None
					def process_esc(match):
						num, = match.groups()
						return chr(int(num, 16))
					line = re.sub(r"(?<!\\)\\x([0-9a-fA-F]{2})", process_esc, line)
					line = line.replace(r'\\', '\\')
					message = None
					if not line.startswith('/'):
						message = Privmsg(client, CONF.channel, line)
					else:
						args = line[1:].split(' ')
						line = lambda: ' '.join(args)
						cmd = args.pop(0)
						if not cmd:
							# "/ TEXT" -> literal privmsg "/TEXT"
							message = Privmsg(client, CONF.channel, '/' + line())
						elif cmd == 'me':
							message = Privmsg.action(client, CONF.channel, line())
						elif cmd in ('msg', 'memsg'):
							constructor = Privmsg.action if cmd == 'memsg' else Privmsg
							if not args:
								# XXX consider displaying an error msg?
								continue
							target = args.pop(0)
							message = constructor(client, target, line())
						elif cmd == 'localexec':
							scope = {}
							exec line() in globals(), scope
							message = scope.get('message', None)
						elif cmd == 'sing':
							message = Privmsg(client, CONF.channel, "\xe2\x99\xab {} \xe2\x99\xab".format(line()))
						elif cmd == 'nick':
							client.nick = line()
						elif cmd == 'quit':
							client.quit(line())
						else:
							message = Message(client, cmd, *args)
					if message:
						message.send()
						generic_recv(client, message, sender=client.nick)
		except EOFError:
			client.quit("Exiting")


if __name__=='__main__':
	main()
