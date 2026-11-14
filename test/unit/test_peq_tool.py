"""tools/peq.py's stock-free parts; compile_common and patch_player link against stock (# pragma: no mutate block).
Not test_peq.py: test/fw has one, and neither folder is a package, so pytest can't collect both."""
import build
import peq


def test_function_macros_call_each_stock_function_at_its_address():
    got = peq.function_macros({'_create_vector': '0x401000u', 'vector_init': '0x402000u', 'not_stock': '0x403000u'})
    # _create_vector is void *(const char *), vector_init void (void *); a name stock lacks gets no macro
    assert got == ['#define _create_vector ((void * (*)(const char *))0x401000u)',
                   '#define vector_init ((void (*)(void *))0x402000u)']
    assert len(peq.function_macros(dict.fromkeys(build.FUNCTIONS, '0'))) == len(build.FUNCTIONS)


def test_unformatted_names_ui_sources_files():
    # tools/format.sh formats UI_SOURCES but these: a misspelt one would leave its file formatted and the rule unread
    assert peq.UNFORMATTED and len(set(peq.UNFORMATTED)) == len(peq.UNFORMATTED)
    stray = set(peq.UNFORMATTED) - set(peq.UI_SOURCES)
    assert not stray, f'not in UI_SOURCES: {sorted(stray)}'
    absent = [f for f in peq.UNFORMATTED if not (build.ROOT/'patch'/f).is_file()]
    assert not absent, f'not under patch/: {absent}'
