"""Box8 commands extend, never replace, Box7's existing controls."""
import re
from box7_commands import VoiceCommand, NUMBERS, normalize, parse_command as base_parse


def parse_command(text):
    value = normalize(text)
    aliases = {'read table': 'table_read', 'read the table': 'table_read',
        'read tables': 'table_read', 'analyze tables': 'table_read',
        'next row': 'table_next', 'read the next row': 'table_next',
        'previous row': 'table_previous', 'repeat row': 'table_repeat',
        'repeat the row': 'table_repeat', 'skip table': 'table_skip',
        'skip this table': 'table_skip', 'read the source': 'qa_source',
        'read source paragraph': 'qa_source', 'read the source paragraph': 'qa_source'}
    if value in aliases:
        return VoiceCommand(aliases[value], text=text)
    match = re.fullmatch(r'(?:read|repeat) (?:the )?(row|column|table) (\w+)', value)
    if match:
        kind, token = match.groups()
        number = int(token) if token.isdecimal() else NUMBERS.get(token)
        if number and 1 <= number <= 999:
            return VoiceCommand('table_' + kind, number, text)
    match = re.fullmatch(r'read (?:the )?(.+?) column', value)
    if match:
        return VoiceCommand('table_column_name', text=match[1])
    base = base_parse(text)
    if base.action == 'unknown' and value.startswith(('tell me ', 'explain ', 'summarize ', 'list ')):
        return VoiceCommand('question', text=text)
    return base
