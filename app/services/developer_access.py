"""Developer tools eligibility from the authenticated account's verified email."""

DEVELOPER_EMAILS = frozenset({
    "daihua.ye@gmail.com",
    "raymond2026ye@gmail.com",
    "anna2012wan@gmail.com",
    "eli.zhankun.ye@gmail.com",
    "raymond2015ye@gmail.com",
    "anna2006wan@gmail.com",
    "daye@vistasolutionsllc.com",
    "vistavalueconstruction@gmail.com",
    "vistasolutions.company@gmail.com",
})


def has_developer_access(email: str) -> bool:
    return email.strip().casefold() in DEVELOPER_EMAILS
