
def instanceclass(cls):
	"""A class decorator for creating classes which then get immediately replaced with
	an instance of the class.
	This is useful for creating namespaces or other things that you want to be
	unique objects but still have instance-like features.
	Class should take no init args.
	"""
	return cls()
