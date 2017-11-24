import py
import sys
from hypothesis import given, strategies, settings, example

from rpython.rlib import rutf8, runicode


@given(strategies.characters(), strategies.booleans())
def test_unichr_as_utf8(c, allow_surrogates):
    i = ord(c)
    if not allow_surrogates and 0xD800 <= i <= 0xDFFF:
        py.test.raises(ValueError, rutf8.unichr_as_utf8, i, allow_surrogates)
    else:
        u = rutf8.unichr_as_utf8(i, allow_surrogates)
        assert u == c.encode('utf8')

@given(strategies.binary())
def test_check_ascii(s):
    raised = False
    try:
        s.decode('ascii')
    except UnicodeDecodeError as e:
        raised = True
    try:
        rutf8.check_ascii(s)
    except rutf8.CheckError:
        assert raised
    else:
        assert not raised

@settings(max_examples=10000)
@given(strategies.binary(), strategies.booleans())
@example('\xf1\x80\x80\x80', False)
def test_check_utf8(s, allow_surrogates):
    _test_check_utf8(s, allow_surrogates)

@given(strategies.text(), strategies.booleans())
def test_check_utf8_valid(u, allow_surrogates):
    _test_check_utf8(u.encode('utf-8'), allow_surrogates)

def _test_check_utf8(s, allow_surrogates):
    def _has_surrogates(s):
        for u in s.decode('utf8'):
            if 0xD800 <= ord(u) <= 0xDFFF:
                return True
        return False

    try:
        u, _ = runicode.str_decode_utf_8(s, len(s), None, final=True,
                                         allow_surrogates=allow_surrogates)
        valid = True
    except UnicodeDecodeError as e:
        valid = False
    length, flag = rutf8._check_utf8(s, allow_surrogates, 0, len(s))
    if length < 0:
        assert not valid
        assert ~(length) == e.start
    else:
        assert valid
        if flag == rutf8.FLAG_ASCII:
            s.decode('ascii') # assert did not raise
        elif flag == rutf8.FLAG_HAS_SURROGATES:
            assert allow_surrogates
            assert _has_surrogates(s)
        if sys.maxunicode == 0x10FFFF or not _has_surrogates(s):
            assert length == len(u)

@given(strategies.characters())
def test_next_pos(uni):
    skips = []
    for elem in uni:
        skips.append(len(elem.encode('utf8')))
    pos = 0
    i = 0
    utf8 = uni.encode('utf8')
    while pos < len(utf8):
        new_pos = rutf8.next_codepoint_pos(utf8, pos)
        assert new_pos - pos == skips[i]
        i += 1
        pos = new_pos

def test_check_newline_utf8():
    for i in xrange(sys.maxunicode):
        if runicode.unicodedb.islinebreak(i):
            assert rutf8.islinebreak(unichr(i).encode('utf8'), 0)
        else:
            assert not rutf8.islinebreak(unichr(i).encode('utf8'), 0)

def test_isspace_utf8():
    for i in xrange(sys.maxunicode):
        if runicode.unicodedb.isspace(i):
            assert rutf8.isspace(unichr(i).encode('utf8'), 0)
        else:
            assert not rutf8.isspace(unichr(i).encode('utf8'), 0)

@given(strategies.characters(), strategies.text())
def test_utf8_in_chars(ch, txt):
    response = rutf8.utf8_in_chars(ch.encode('utf8'), 0, txt.encode('utf8'))
    r = (ch in txt)
    assert r == response

@given(strategies.text(), strategies.integers(min_value=0),
                          strategies.integers(min_value=0))
def test_codepoints_in_utf8(u, start, len1):
    end = start + len1
    if end > len(u):
        extra = end - len(u)
    else:
        extra = 0
    count = rutf8.codepoints_in_utf8(u.encode('utf8'),
                                     len(u[:start].encode('utf8')),
                                     len(u[:end].encode('utf8')) + extra)
    assert count == len(u[start:end])

@given(strategies.text())
def test_utf8_index_storage(u):
    index = rutf8.create_utf8_index_storage(u.encode('utf8'), len(u))
    for i, item in enumerate(u):
        assert (rutf8.codepoint_at_index(u.encode('utf8'), index, i) ==
                ord(item))

@given(strategies.text())
@example(u'x' * 64 * 5)
@example(u'x' * (64 * 5 - 1))
def test_codepoint_position_at_index(u):
    index = rutf8.create_utf8_index_storage(u.encode('utf8'), len(u))
    for i in range(len(u) + 1):
        assert (rutf8.codepoint_position_at_index(u.encode('utf8'), index, i) ==
                len(u[:i].encode('utf8')))

repr_func = rutf8.make_utf8_escape_function(prefix='u', pass_printable=False,
                                            quotes=True)

@given(strategies.text())
def test_repr(u):
    assert repr(u) == repr_func(u.encode('utf8'))

@given(strategies.lists(strategies.characters()))
@example([u'\ud800', u'\udc00'])
def test_surrogate_in_utf8(unichars):
    uni = ''.join([u.encode('utf8') for u in unichars])
    result = rutf8.surrogate_in_utf8(uni)
    expected = any(uch for uch in unichars if u'\ud800' <= uch <= u'\udfff')
    assert result == expected

@given(strategies.lists(strategies.characters()))
def test_get_utf8_length_flag(unichars):
    u = u''.join(unichars)
    exp_lgt = len(u)
    exp_flag = rutf8.FLAG_ASCII
    for c in u:
        if ord(c) > 0x7F:
            exp_flag = rutf8.FLAG_REGULAR
        if 0xD800 <= ord(c) <= 0xDFFF:
            exp_flag = rutf8.FLAG_HAS_SURROGATES
            break
    lgt, flag = rutf8.get_utf8_length_flag(''.join([c.encode('utf8') for c in u]))
    if exp_flag != rutf8.FLAG_HAS_SURROGATES:
        assert lgt == exp_lgt
    assert flag == exp_flag

def test_utf8_string_builder():
    s = rutf8.Utf8StringBuilder()
    s.append("foo")
    s.append_char("x")
    assert s.get_flag() == rutf8.FLAG_ASCII
    assert s.get_length() == 4
    assert s.build() == "foox"
    s.append(u"\u1234".encode("utf8"))
    assert s.get_flag() == rutf8.FLAG_REGULAR
    assert s.get_length() == 5
    assert s.build().decode("utf8") == u"foox\u1234"
    s.append("foo")
    s.append_char("x")
    assert s.get_flag() == rutf8.FLAG_REGULAR
    assert s.get_length() == 9
    assert s.build().decode("utf8") == u"foox\u1234foox"
    s = rutf8.Utf8StringBuilder()
    s.append_code(0x1234)
    assert s.build().decode("utf8") == u"\u1234"
    assert s.get_flag() == rutf8.FLAG_REGULAR
    assert s.get_length() == 1
    s.append_code(0xD800)
    assert s.get_flag() == rutf8.FLAG_HAS_SURROGATES
    assert s.get_length() == 2

@given(strategies.text())
def test_utf8_iterator(arg):
    u = rutf8.Utf8StringIterator(arg.encode('utf8'))
    l = []
    while not u.done():
        l.append(unichr(u.next()))
    assert list(arg) == l