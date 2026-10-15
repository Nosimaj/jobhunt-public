"""Workday career sites (the public CXS API). See CLAUDE.md, "Workday"."""

import json
from pathlib import Path

import pytest

from jobhunt.discovery.workday import MAX_PAGES, PAGE_SIZE, list_jobs, parse_token

FIX = Path(__file__).parent / "fixtures" / "ats"


def test_a_plain_token_parses():
    b = parse_token("massgeneralbrigham.wd1/MGBExternal")
    assert (b.tenant, b.wd, b.site, b.locations) == (
        "massgeneralbrigham", "wd1", "MGBExternal", ())
    assert b.list_url == ("https://massgeneralbrigham.wd1.myworkdayjobs.com"
                          "/wday/cxs/massgeneralbrigham/MGBExternal/jobs")
    assert b.detail_url("/job/Boston/X_RQ1") == (
        "https://massgeneralbrigham.wd1.myworkdayjobs.com"
        "/wday/cxs/massgeneralbrigham/MGBExternal/job/Boston/X_RQ1")
    assert b.facets() == {}


def test_location_facets_parse():
    b = parse_token("exampleco.wd1/careers?locations=abc123,def456")
    assert b.locations == ("abc123", "def456")
    assert b.facets() == {"locations": ["abc123", "def456"]}


def test_a_three_digit_instance_parses():
    assert parse_token("exampleco.wd503/External").wd == "wd503"


@pytest.mark.parametrize("bad", [
    "massgeneralbrigham/MGBExternal",        # no wdN
    "massgeneralbrigham.wd1",                # no site
    "massgeneralbrigham.wdx/MGBExternal",    # wd not numeric
    "exampleco.wd1/careers?locations=",      # empty facet list
    "exampleco.wd1/careers?sites=abc",       # unknown facet key
])
def test_malformed_tokens_are_refused(bad):
    with pytest.raises(ValueError):
        parse_token(bad)


def pages(*counts, total_first=None):
    """A fake `post` serving pages of the given sizes, then an empty page.
    `total` is populated only on offset 0, like the real API."""
    calls = []
    if total_first is None:
        total_first = sum(counts)
    def post(url, body):
        calls.append(body)
        i = body["offset"] // PAGE_SIZE
        n = counts[i] if i < len(counts) else 0
        rows = [{"title": f"Role {i}-{k}", "externalPath": f"/job/X/R{i}_{k}"}
                for k in range(n)]
        return {"total": total_first if body["offset"] == 0 else 0,
                "jobPostings": rows}
    return post, calls


def test_paging_stops_on_an_empty_page_not_on_total():
    post, calls = pages(20, 20, 7, total_first=20)   # total lies, as real tenants do
    rows = list_jobs(parse_token("t.wd1/s"), post)
    assert len(rows) == 47
    assert [c["offset"] for c in calls] == [0, 20, 40]   # 7 rows = last page
    assert all(c["limit"] == PAGE_SIZE == 20 for c in calls)


def test_the_location_facets_are_sent():
    post, calls = pages(1)
    list_jobs(parse_token("exampleco.wd1/careers?locations=abc"), post)
    assert calls[0]["appliedFacets"] == {"locations": ["abc"]}
    assert calls[0]["searchText"] == ""


def test_a_board_that_never_ends_is_refused():
    def post(url, body):                       # always a FULL page of new rows
        o = body["offset"]
        return {"jobPostings": [{"title": "x", "externalPath": f"/job/x{o + k}"}
                                for k in range(PAGE_SIZE)]}
    with pytest.raises(RuntimeError, match="pages"):
        list_jobs(parse_token("t.wd1/s"), post)
    assert MAX_PAGES > 1


def test_a_real_list_page_has_the_fields_we_read():
    page = json.loads((FIX / "workday-mgb-list.json").read_text())
    row = page["jobPostings"][0]
    assert row["title"] and row["externalPath"].startswith("/job/")


# ---- detail + fetch_workday -------------------------------------------------

from jobhunt.discovery.ats import BoardError
from jobhunt.discovery.workday import fetch_workday, parse_detail, role_id

DETAIL = json.loads((FIX / "workday-mgb-detail.json").read_text())


def detail_with(**info):
    return {"jobPostingInfo": {"title": "T", "location": "Boston, MA",
                               "jobDescription": "<p>" + "duties " * 60 + "</p>",
                               "externalUrl": "https://x/apply", **info}}


def test_a_real_detail_becomes_a_role():
    r = parse_detail("massgeneralbrigham.wd1/MGBExternal", "Mass General Brigham",
                     DETAIL, "fallback")
    assert r.provider == "workday" and r.company == "Mass General Brigham"
    assert len(r.body) > 200 and "<" not in r.body
    assert r.title and r.apply_url
    assert len(r.posted_at) == 10


def test_a_real_draper_detail_is_onsite():
    d = json.loads((FIX / "workday-draper-detail.json").read_text())
    assert parse_detail("draper.wd5/Draper_Careers", "Draper", d, "x").workplace_type == "onsite"


@pytest.mark.parametrize("remote_type, expected", [
    ("Remote", "remote"), ("Hybrid", "hybrid"), ("Onsite Required", "onsite"),
    (None, "unknown")])
def test_workplace_type_comes_from_remote_type(remote_type, expected):
    r = parse_detail("t.wd1/s", "C", detail_with(remoteType=remote_type), "T")
    assert r.workplace_type == expected


def test_a_detail_without_a_description_is_none():
    assert parse_detail("t.wd1/s", "C", detail_with(jobDescription=None), "T") is None


def test_the_id_keys_on_the_full_token_and_the_job_slug():
    assert role_id("exampleco.wd1/careers?locations=a", "/job/Boston/Designer_R123") == \
        "workday:exampleco.wd1/careers?locations=a:Designer_R123"


def listing(*titles):
    def post(url, body):
        if body["offset"]:
            return {"jobPostings": []}
        return {"jobPostings": [{"title": t, "externalPath": f"/job/B/{t.replace(' ', '_')}"}
                                for t in titles]}
    return post


def detail_get(calls, fail_on=()):
    def get(url):
        calls.append(url)
        if any(f in url for f in fail_on):
            raise RuntimeError("500")
        return detail_with(title=url.rsplit("/", 1)[-1].replace("_", " "))
    return get


def test_prefiltered_and_known_titles_are_never_detail_fetched():
    """Both still count as LISTED, so closure leaves them open."""
    calls = []
    known = {role_id("t.wd1/s", "/job/B/Senior_Product_Designer")}
    res = fetch_workday("t.wd1/s", "C",
                        post=listing("Senior Product Designer",
                                     "Registered Nurse", "UX Researcher"),
                        get=detail_get(calls), known_ids=known, pause=0)
    assert [c.rsplit("/", 1)[-1] for c in calls] == ["UX_Researcher"]
    assert [r.title for r in res.roles] == ["UX Researcher"]
    assert len(res.listed_ids) == 3
    assert res.prefiltered == 1


def test_a_failed_detail_skips_one_role_not_the_board():
    res = fetch_workday("t.wd1/s", "C", post=listing("UX Designer", "UI Designer"),
                        get=detail_get([], fail_on=("UX_Designer",)),
                        known_ids=set(), pause=0)
    assert [r.title for r in res.roles] == ["UI Designer"]
    assert res.skipped == 1 and len(res.listed_ids) == 2


def test_a_list_failure_on_a_later_page_fails_the_whole_board():
    """A partial list must never reach reconcile_board."""
    def post(url, body):
        if body["offset"] == 0:                # a FULL page, so page 2 is fetched
            return {"jobPostings": [{"title": "UX Designer", "externalPath": f"/job/B/a{k}"}
                                    for k in range(20)]}
        raise RuntimeError("502 on page 2")
    with pytest.raises(BoardError, match="C"):
        fetch_workday("t.wd1/s", "C", post=post, get=detail_get([]),
                      known_ids=set(), pause=0)


def test_a_bad_token_is_a_board_error():
    with pytest.raises(BoardError):
        fetch_workday("nope", "C", post=listing(), get=detail_get([]),
                      known_ids=set(), pause=0)


# ---- a short list must never pass as complete --------------------------------

def test_an_error_body_is_not_the_end_of_the_list():
    """A 200 with no `jobPostings` key is an error, not an empty page -
    reading it as the end would close every row not yet listed."""
    def post(url, body):
        if body["offset"] == 0:
            return {"total": 40, "jobPostings": [
                {"title": "x", "externalPath": f"/job/x{k}"} for k in range(20)]}
        return {"errorCode": "HTTP_500"}
    with pytest.raises(RuntimeError, match="jobPostings"):
        list_jobs(parse_token("t.wd1/s"), post)


def test_a_list_far_short_of_total_is_refused():
    post, _ = pages(20, total_first=200)          # ends after 20 of 200
    with pytest.raises(RuntimeError, match="short"):
        list_jobs(parse_token("t.wd1/s"), post)


def test_total_capped_at_2000_does_not_refuse_a_longer_list():
    post, _ = pages(*([20] * 101), total_first=2000)   # 2,020 rows
    assert len(list_jobs(parse_token("t.wd1/s"), post)) == 2020


def test_a_row_listed_twice_is_kept_once():
    """Rows shift while paging when a posting is removed mid-walk."""
    def post(url, body):
        if body["offset"] == 0:
            return {"total": 2, "jobPostings": [{"title": "a", "externalPath": "/job/a"},
                                                {"title": "b", "externalPath": "/job/b"}]}
        if body["offset"] == 20:
            return {"jobPostings": [{"title": "b", "externalPath": "/job/b"}]}
        return {"jobPostings": []}
    assert [r["externalPath"] for r in list_jobs(parse_token("t.wd1/s"), post)] == ["/job/a", "/job/b"]


def test_a_tenant_that_wraps_to_page_one_stops_at_the_short_page():
    """Some tenants (seen live with a location facet) serve page 0 again past
    the end instead of an empty page, so 'until an empty page' ran to
    MAX_PAGES. The last real page is a short one."""
    calls = []
    def post(url, body):
        calls.append(body["offset"])
        off = body["offset"] if body["offset"] < 40 else 0       # wraps
        n = 20 if off < 20 else 9
        return {"total": 29 if off == 0 else 0, "jobPostings": [
            {"title": "x", "externalPath": f"/job/r{off + k}"} for k in range(n)]}
    rows = list_jobs(parse_token("t.wd1/s?locations=a"), post)
    assert len(rows) == 29 and calls == [0, 20]


def test_a_page_of_only_repeats_ends_the_walk():
    def post(url, body):
        return {"total": 20, "jobPostings": [
            {"title": "x", "externalPath": f"/job/r{k}"} for k in range(20)]}
    assert len(list_jobs(parse_token("t.wd1/s"), post)) == 20
