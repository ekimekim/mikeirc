
def smart_reset(s):
	"""Modify a string to have smarter color reset from terminal escapes.
	In particular, a reset code will return it to the color previous to it, instead of fully reset.
	"""
	stack = []
	parts = s.split('\x1b[')
	ret = parts[0]
	for part in parts[1:]:
		code, text = part.split('m', 1)
		if code in ('', '0'):
			ret += '\x1b[m'
			if stack:
				stack.pop()
			if stack:
				prevcode = stack[-1]
				ret += '\x1b[' + prevcode + 'm'
		else:
			stack.append(code)
			ret += '\x1b[' + code + 'm'
		ret += text
	return ret
