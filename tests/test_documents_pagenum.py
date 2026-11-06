from __future__ import annotations

from athena.documents.pagenum import pdf_page_of, printed_numbers, raw_number


def page(n_printed: int | None, body: str = "some text of the page that goes on for a while", where: str = "top") -> str:
    if n_printed is None:
        return body
    return f"{n_printed}\n{body}" if where == "top" else f"{body}\n{n_printed}"


def test_the_printed_number_is_a_line_of_its_own_at_the_top_or_bottom_and_nothing_else() -> None:
    assert raw_number("20\nSECTION II - RISK FACTORS\nAn investment involves risk") == 20
    assert raw_number("text of a page\nmore text\n112 ") == 112
    assert raw_number("Page 44 of 500\ntext") == 44
    assert raw_number("text 20 more\nlines of 2026 text") is None  # a figure inside a line is not a page number
    assert raw_number("a\nb\nc\nd\n7\ne\nf\ng\n") is None  # the middle of a page is not looked at
    assert raw_number("0\ntext") is None and raw_number("") is None


def test_an_offset_confirmed_by_a_neighbour_gives_the_number_and_a_gap_between_agreeing_pages_is_filled() -> None:
    pages = [(1, "cover without a number"), (2, "contents without a number")] + [(p, page(p - 5)) for p in range(3, 20) if p != 9] + [(9, page(None))]
    got = printed_numbers(sorted(pages))
    assert got[6] == 1 and got[19] == 14 and got[9] == 4  # 9 has no number of its own: the pages either side agree on the offset of 5
    assert 1 not in got and 2 not in got  # the cover and the contents have none


def test_a_lone_figure_that_agrees_with_no_neighbour_is_not_taken_for_a_page_number() -> None:
    pages = [(p, page(p - 5)) for p in range(6, 16)]
    pages[3] = (9, page(2026))  # a year on a line of its own in the middle of the numbering
    got = printed_numbers(pages)
    assert 9 in got and got[9] == 4  # filled from the pages around it, not taken from the year
    alone = printed_numbers([(1, page(2026)), (2, "text"), (3, "text")])
    assert alone == {}


def test_numbering_that_restarts_is_read_in_its_own_block_and_a_citation_of_a_repeated_number_is_not_guessed() -> None:
    first = [(p, page(p - 5)) for p in range(6, 14)]  # printed 1 to 8
    second = [(p, page(p - 13)) for p in range(14, 22)]  # the annexure restarts at 1
    numbers = printed_numbers(first + second)
    assert numbers[6] == 1 and numbers[14] == 1 and numbers[21] == 8
    assert pdf_page_of(1, numbers) is None  # two parts print a 1: not guessed
    assert pdf_page_of(9, numbers) is None  # printed nowhere
    clean = printed_numbers([(p, page(p - 5)) for p in range(6, 14)])
    assert pdf_page_of(4, clean) == 9 and pdf_page_of(8, clean) == 13
