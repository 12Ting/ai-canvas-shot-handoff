import unicodedata


def normalize_branch_name(value: str) -> tuple[str, str]:
    display_name = value.strip()
    normalized_name = unicodedata.normalize("NFKC", display_name).casefold()
    if not display_name or not normalized_name:
        raise ValueError("branch name must not be empty")
    if len(display_name) > 100 or len(normalized_name) > 100:
        raise ValueError("branch name must not exceed 100 characters")
    return display_name, normalized_name
