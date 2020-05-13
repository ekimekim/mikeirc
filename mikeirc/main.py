import gevent.monkey
gevent.monkey.patch_all()

import logging
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
from lineedit import LineEditing, complete_from
from pyconfig import CONF

import irccolors


COMMAND_HIGHLIGHT = "30"
KICK_HIGHLIGHT = "35"
PRIVATE_HIGHLIGHT = "1"
NICK_HIGHLIGHT = "31;1"
USER_HIGHLIGHT = "32"
OP_HIGHLIGHT = "33"
TWITCH_EMOTE_HIGHLIGHT = "36"
SOFT_IGNORE_HIGHLIGHT = "30"

SENDER_WIDTH = 12
USER_WIDTH = 12
REDRAW_LINES = 100

USER_HIGHLIGHTS = {
	'BidServ': '1;33',
	'Bidbot': '1;33',
	'twitchnotify': '33',
	'MLeeLunsford': '1',
	'Ashton': '1',
}
KEYWORD_HIGHLIGHTS = {
	'ekim': NICK_HIGHLIGHT, # das me
	'VST': NICK_HIGHLIGHT,
	'DBVideoStrikeTeam': NICK_HIGHLIGHT,
}
REGEX_HIGHLIGHTS = {
	'DB_.*': '1',
	'.*_LRR': '1',
}

EXCLUDE_NUMERICS = {5}

# for VST
MACROS = {
	"videos":
		"Missed any of DB2019 and want to catch up? The VST has you covered, with full event logs and youtube videos. Check it out! https://db13.vst.ninja",
	"poster":
		"Miss a poster update? Check out https://vst.ninja/DB13/posters/ to see the poster versions as it grows! Or check https://vst.ninja/DB13/postermap to see the clips that the poster references! Here's the latest poster: https://vst.ninja/DB13/posters/latest.png",
	"stats":
		"Fun stats generated from the chat, for all your inner math nerd needs! http://chatstats.vst.ninja",
	"milestones":
		"Want to see how close we are to various milestones? Check out https://vst.ninja/milestones/",
	"gifs":
		"Our Giffers work around the clock to make you say 'I remember that!' or 'Wait, when did that happen?'. Check out their efforts at http://greywool.com/desertbus/2019/gifs/",
	"picnic":
		"Hey folks. While you *calmly* wait for the stream's inevitable return, why not take the opportunity to catch up on some stuff you missed? https://vst.ninja/DB13/",
	"missing":
		"Have you been a DesertBus fan for a long time? Do you like to hoard things like video captures? See if you can help us complete our archive at http://vst.ninja/missingdata/",
}
for word in {
	'milestone', 'poster', 'video', 'videos', 'stats', 'graphs', 'youtube', 'clip', 'postermap',
	'sheet', 'strike', 'missed', 'timeline', 'upload', 'uploaded', 'link', 'request', 'clip',
}:
	KEYWORD_HIGHLIGHTS[word] = KICK_HIGHLIGHT

TWITCH_EVENT_SERVERS = {
	'192.16.64.143',
	'192.16.64.150',
	'192.16.71.221',
	'192.16.71.236',
	'199.9.252.54',
}

CLEAN_QUIT_TIMEOUT = 1

USER_HIGHLIGHTS = {nick.lower(): highlight for nick, highlight in USER_HIGHLIGHTS.items()}

replay_history = []

def read():
	fd = sys.stdin.fileno()
	r,w,x = select([fd], [], [])
	assert fd in r
	return os.read(fd, 1)


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
	port = int(CONF.port or 6667)
	backdoor = CONF.backdoor
	twitch = CONF.twitch
	password = CONF.password

	log_args = {
		'level': CONF.get('log', 'WARNING').upper(),
	}
	if CONF.log_file:
		log_args['filename'] = CONF.log_file
	logging.basicConfig(**log_args)

	# resolve password config options to actual password values
	if password is None and not CONF.no_auth:
		password = getpass("Password for {}: ".format(CONF.nick))
	if not password: # password == '' is different to password == None
		password = None
	if twitch:
		nickserv_password = None
	elif password:
		nickserv_password = "{} {}".format(CONF.email, password) if CONF.email else password
		password = None
	else:
		nickserv_password = None

	if backdoor:
		if backdoor is True:
			backdoor = 1235
		gtools.backdoor(backdoor)

	if twitch:
		# make changes to host
		if not isinstance(twitch, basestring):
			print "Loading chat server for channel..."
			resp = requests.get('http://tmi.twitch.tv/servers', params={'channel': CONF.channel.lstrip('#')})
			resp.raise_for_status()
			servers = resp.json()['servers']
			server = random.choice(servers)
			host, _ = server.split(':')
			print "Using twitch server:", host
		elif twitch == 'event':
			host = random.choice(TWITCH_EVENT_SERVERS)
			print 'Using twitch event server:', host
		else:
			host = twitch
			print 'Using custom twitch server:', host

		# make channel owner bold
		USER_HIGHLIGHTS[CONF.channel.lstrip('#').lower()] = '1'

	client = None
	backoff = Backoff(0.2, 10, 2)
	while True:
		try:
			client = Client(host, CONF.nick, port, real_name=CONF.real_name,
							password=password, nickserv_password=nickserv_password, twitch=twitch, ssl=CONF.ssl)

			channel = client.channel(CONF.channel)
			channel.join()

			editor = LineEditing(input_fn=read, completion=lambda prefix: complete_from(channel.users.users)(prefix.lower()), gevent_handle_sigint=True)

			client.handler(lambda client, msg: generic_recv(editor, client, msg))

			client.start()
			# spawn input greenlet in client's Group, linking its lifecycle to the client
			client._group.spawn(in_worker, client, editor)

			backoff.reset() # successful startup
			client.wait_for_stop()
		except Exception:
			traceback.print_exc()
			time = backoff.get()
			print "retrying in %.2f seconds..." % time
			gevent.sleep(time)
		else:
			break
		finally:
			if client:
				try:
					with gevent.Timeout(CLEAN_QUIT_TIMEOUT):
						client.quit("Quitting")
				except (Exception, KeyboardInterrupt, gevent.Timeout) as ex:
					try:
						client.stop(ex)
					except Exception:
						pass


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

def compose_re_any(regexes):
	"""Compose a list of regexes into a single regex that matches if any of the input regexes match."""
	if not regexes:
		return '$^' # match nothing
	return '|'.join('({})'.format(n) for n in regexes)

def generic_recv(editor, client, msg, sender=None):

	params = msg.params
	text = ' '.join(msg.params)
	is_action = False
	quiet = CONF.quiet
	nousers = CONF.nousers
	empty = ''
	ignore_nick_re = '^({})$'.format(compose_re_any(CONF.ignore_nicks))
	soft_ignore_nick_re = '^({})$'.format(compose_re_any(CONF.soft_ignore_nicks))

	# On twitch, sender is lowercased but display-name is correct, for ascii names.
	# For eg. chinese names, the display name is the chinese characters and the sender is the ascii username.
	# For this case, we display both.
	if not sender:
		sender = (msg.tags and msg.tags.get('display-name')) or msg.sender
		if msg.sender and sender.lower() != msg.sender.lower():
			sender = '{}({})'.format(sender, msg.sender)

	if sender and re.match(ignore_nick_re, sender):
		return

	highlight = lambda outstr, sequence: '\x1b[{}m{}\x1b[m'.format(sequence, outstr)
	def highlight(outstr, sequence):
		fmt = '\x1b[{}m{}\x1b[m'
		if isinstance(outstr, unicode):
			fmt = fmt.decode('utf-8')
		return fmt.format(sequence, outstr)

	# default outstr
	outstr = highlight("{sender:>{SENDER_WIDTH}}: {msg.command} {text}", COMMAND_HIGHLIGHT)

	nosend = False

	if msg.command == 'PRIVMSG':
		target, text = msg.target, msg.payload

		if msg.ctcp:
			ctcp_command, ctcp_arg = msg.ctcp
			if ctcp_command == 'ACTION':
				is_action = True
				text = ctcp_arg

		if CONF.twitch and msg.tags and msg.tags.get('emotes'):
			ranges = []
			try:
				emotes = msg.tags['emotes'].split('/')
				for emote in emotes:
					emote_id, emote_ranges = emote.split(':')
					for emote_range in emote_ranges.split(','):
						start, end = emote_range.split('-')
						ranges.append((int(start), int(end)))
			except ValueError:
				logging.warning("Malformed emotes tag: {!r}".format(msg.tags['emotes']), exc_info=True)
			# counting code points is correct, but might not be possible if invalid
			try:
				text = text.decode('utf-8')
			except UnicodeDecodeError:
				new_text = ''
			else:
				new_text = u''
			pos = 0
			for start, end in sorted(ranges):
				if start < pos:
					logging.warning("Overlapping emotes? emotes={!r}".format(msg.tags['emotes']))
					continue
				# add non-emote text between previous position and start
				new_text += text[pos:start]
				# add the emote text (note start-end is inclusive, so we +1 to make it range correctly)
				new_text += highlight(text[start:end+1], TWITCH_EMOTE_HIGHLIGHT)
				# set pos for next loop
				pos = end + 1
			# add final non-emote part after last emote
			new_text += text[pos:]
			text = new_text
			if isinstance(text, unicode):
				text = text.encode('utf-8')

		if target == CONF.channel:
			if is_action:
				outstr = "{sender:>{SENDER_WIDTH}} {text}"
			else:
				outstr = "{sender:>{SENDER_WIDTH}}: {text}"
			if sender.lower() in USER_HIGHLIGHTS:
				outstr = highlight(outstr, USER_HIGHLIGHTS[sender.lower()])
			match = re.match(soft_ignore_nick_re, sender)
			logging.debug("checking if {!r} matches {!r} for soft ignore: {}".format(sender, soft_ignore_nick_re, bool(match)))
			if match:
				outstr = highlight("{sender:>{SENDER_WIDTH}} said something", SOFT_IGNORE_HIGHLIGHT)
		else:
			# private message
			sender = "[{}]".format(sender)
			if not client.matches_nick(target):
				text = '[{}] {}'.format(target, text)
			if is_action:
				outstr = highlight("{sender:>{SENDER_WIDTH}} {text}", PRIVATE_HIGHLIGHT)
			else:
				outstr = highlight("{sender:>{SENDER_WIDTH}}: {text}", PRIVATE_HIGHLIGHT)
	elif msg.command == 'QUIT':
		outstr = highlight("{sender:>{SENDER_WIDTH}} quits: {text}", COMMAND_HIGHLIGHT)
		if quiet or nousers: nosend = True
	elif msg.command == 'NICK':
		target, text = params[0], ' '.join(params[1:])
		outstr = highlight("{sender:>{SENDER_WIDTH}} changes their name to {target}", COMMAND_HIGHLIGHT)
		if quiet or nousers: nosend = True
	elif msg.command == 'KICK':
		chan, target, text = params[0], params[1], ' '.join(params[2:])
		outstr = highlight("{empty:>{SENDER_WIDTH}} {target} kicked by {sender}: {text}", KICK_HIGHLIGHT)
	elif msg.command == 'CLEARCHAT':
		chan, target = params
		text = msg.tags.get('ban-reason', '<no message>')
		duration = msg.tags.get('ban-duration')
		dur_text = 'timed out for {}s'.format(duration) if duration is not None else 'banned'
		outstr = highlight("{empty:>{SENDER_WIDTH}} {target} {dur_text}: {text}", KICK_HIGHLIGHT)
	elif msg.command == 'ROOMSTATE':
		changes = ', '.join("{}={!r}".format(k, v) for k, v in msg.tags.items())
		outstr = highlight("{empty:>{SENDER_WIDTH}} Room state change: {changes}", KICK_HIGHLIGHT)
	elif msg.command == 'USERNOTICE' and msg.tags['msg-id'] == 'resub':
		sender = msg.tags['login']
		months = msg.tags['msg-param-months']
		if len(msg.params) == 2:
			chan, text = msg.params
			suffix = ': {}'.format(text)
		else:
			suffix = ''
		outstr = highlight("{sender:>{SENDER_WIDTH}} subscribed for {months} months{suffix}", PRIVATE_HIGHLIGHT)
	elif msg.command in ('PING', 'PONG', 'USERSTATE'):
		return
	else:
		if quiet: nosend = True
		if nousers and msg.command in ('NAMES', 'JOIN', 'PART', 'MODE', '353', '366'): nosend = True
		try:
			n = int(msg.command)
		except ValueError:
			# unknown message type
			pass
		else:
			# numeric command - unless excluded, print
			if n in EXCLUDE_NUMERICS: return
			if sender == client.hostname and params and client.matches_nick(params[0]):
				outstr = highlight("{msg.command:>{SENDER_WIDTH}}: {text}", COMMAND_HIGHLIGHT)
			else:
				# not sure what circumstances this would apply for, use default
				pass
	if not nosend:
		kwargs = globals().copy()
		kwargs.update(locals())
		out(editor, client, outstr.format(**kwargs))


def out(editor, client, s):
	channel = client.channel(CONF.channel)

	# scan for regexes
	for regex, highlight in REGEX_HIGHLIGHTS.items():
		if isinstance(regex, basestring):
			regex = re.compile(regex, flags=re.I)
		def wrap_it(match):
			return '\x1b[{}m{}\x1b[m'.format(highlight, match.group())
		s = regex.sub(wrap_it, s)

	# highlight nick
	keywords = {}
	keywords.update({user: USER_HIGHLIGHT for user in channel.users.users})
	keywords.update({user: OP_HIGHLIGHT for user in channel.users.ops})
	keywords.update({nick_normalize(client._nick): NICK_HIGHLIGHT})
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
	line = irccolors.apply_irc_formatting(outbuf)
	global replay_history
	replay_history = (replay_history + [line])[:REDRAW_LINES]
	editor.write(line)


def in_worker(client, editor):
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
							scope = {'client':client}
							exec line() in globals(), scope
							message = scope.get('message', None)
						elif cmd == 'sing':
							message = Privmsg(client, CONF.channel, "\xe2\x99\xab {} \xe2\x99\xab".format(line()))
						elif cmd == 'nick':
							client.nick = line()
						elif cmd == 'quit':
							client.quit(line())
						elif cmd == 'redraw':
							for line in replay_history:
								editor.write(line)
						elif cmd in MACROS:
							message = Privmsg(client, CONF.channel, MACROS[cmd])
						else:
							message = Message(client, cmd, *args)
					if message:
						message.send()
						generic_recv(editor, client, message, sender=client.nick)
		except EOFError:
			client.quit("Exiting")


if __name__=='__main__':
	main()
