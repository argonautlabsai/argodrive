"""Persist a ds4 editor draft as data only. Never source it or launch an engine."""

TEXT_FIELDS = {'buildId', 'threads', 'cache', 'prefetch', 'decodeCachePct'}
CHOICES = {'engine': {'ds4'}, 'backend': {'metal'}, 'build': {'standard', 'argonaut'},
           'modelFamily': {'glm', 'deepseek', 'deepseek41', 'other'}, 'objective': {'decode', 'prefill', 'balanced'},
           'readAhead': {'default', 'off'}, 'method': {'single', 'split', 'hashed'},
           'nocache': {'default', 'on', 'off'}, 'lru': {'default', 'on'},
           'prefill': {'default', 'selected'}}


def draft_shape(value):
    """Bound storage shape. Incomplete candidates are allowed; this is not qualification."""
    keys = TEXT_FIELDS | set(CHOICES) | {'schema', 'cold', 'sources'}
    if not isinstance(value, dict) or set(value) != keys or type(value.get('schema')) is not int or value['schema'] != 1:
        raise ValueError('Unsupported engine draft schema')
    def string(text):
        return isinstance(text, str) and len(text) <= 4096 and not any(ord(c) < 32 or ord(c) == 127 for c in text)
    for key, choices in CHOICES.items():
        if not isinstance(value[key], str) or value[key] not in choices:
            raise ValueError('Unsupported engine draft field: ' + key)
    if any(not string(value[key]) for key in TEXT_FIELDS) or type(value['cold']) is not bool:
        raise ValueError('Invalid engine draft field type')
    sources = value['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8:
        raise ValueError('A draft supports 1–8 sources')
    for source in sources:
        if not isinstance(source, dict) or set(source) != {'path', 'weight', 'pieces', 'inflight'} or any(not string(v) for v in source.values()):
            raise ValueError('Invalid engine source draft')
    return value
