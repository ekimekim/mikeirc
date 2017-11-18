import itertools
import logging
import re

# for non-color formats, maps character to SGR escape string
FORMAT_MAP = {
	'\x02': '1', # bold
	'\x1f': '4', # underline
	'\x16': '7', # reverse fore/background
	'\x1d': None, # italic - not supported
}

# maps colors in color format specifiers to SGR colors 0-7
# note this is a lossy conversion
COLOR_MAP = {
	0: 7, # white
	1: 0, # black
	2: 4, # blue (navy)
	3: 2, # green
	4: 1, # red
	5: 3, # brown (maroon)
	6: 5, # purple
	7: 3, # orange (olive)
	8: 3, # yellow
	9: 2, # light green (lime)
	10: 6, # teal (a green/blue cyan)
	11: 6, # light cyan (cyan) (aqua)
	12: 6, # light blue (royal)
	13: 5, # pink (light purple) (fuchsia)
	14: 0, # grey
	15: 7, # light grey (silver)
}
COLOR_CHAR = '\x03'
RESET_CHAR = '\x0f'


def apply_irc_formatting(line):
	"""
	This function takes in a string that may contain irc format chars and
	SGR terminal escape sequences (matching /CSI '[' (\d+ ';')* \d+ 'm' /).
	It will interleave them according to the following rules:
		* A terminal escape applies on top of existing state, and saves existing state to the stack
		* A terminal reset restores the previous state from the top of the stack
		* An IRC format character will toggle an effect (non-color), set a color or reset irc format state
			This applies on top of any terminal escape state
	"""
	eat = lambda s, n: (s[:n], s[n:])
	output = ''
	irc_formats = set()
	irc_colors = None, None

	# stack of list of SGR format numbers
	# we abuse that with mutually incompatible options, last wins
	# so we just output the entire stack concat'ed
	terminal_stack = []

	while line:
		c, line = eat(line, 1)

		if c in FORMAT_MAP:
			irc_formats ^= {c} # toggle state
			
		elif c == COLOR_CHAR:
			match = re.match('([0-9]{1,2})?(?:,([0-9]{1,2}))?(.*)', line)
			if not match:
				logging.warning("did not get a color match, this shouldn't be possible: {!r}".format(line))
				continue
			fore, back, line = match.groups()
			get_color = lambda color: COLOR_MAP.get(int(color), '9') # default to reset for out of range
			if (fore, back) == (None, None):
				irc_colors = None, None
			else:
				fore = irc_colors[0] if fore is None else get_color(fore)
				back = irc_colors[1] if back is None else get_color(back)
				irc_colors = fore, back

		elif c == RESET_CHAR:
			irc_colors = None, None
			irc_formats = set()

		elif c == '\x1b':
			# potential SGR sequence
			match = re.match(r'\[ (?P<formats> ((\d+;)*\d+)? ) m (?P<remainder> .*)', line, re.VERBOSE)
			if match:
				formats = match.group('formats').split(';')
				line = match.group('remainder')
				if formats in ([''], [0]):
					# got a reset (empty or only zero)
					if terminal_stack:
						terminal_stack.pop()
				else:
					terminal_stack.append(formats)
			else:
				# not a SGR sequence, pass it through
				output += c
				continue

		else:
			# pass it through
			output += c
			continue

		# if we didn't pass through, we changed format state. write the new format escape.
		formats = [0] # always start with a reset
		formats += list(itertools.chain.from_iterable(terminal_stack)) # flatten stack
		formats += filter(None, [FORMAT_MAP[char] for char in irc_formats]) # add any irc formats
		# add any irc colors
		for color, base in zip(irc_colors, [30, 40]):
			if color:
				formats.append(base + color)

		output += "\x1b[{}m".format(";".join(map(str, formats)))

	# No matter what the final state was, always end with a true reset
	return output + '\x1b[m'
