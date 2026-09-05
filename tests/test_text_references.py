from ziplex.text_references import find_text_references, find_text_references_for_file, _contains_token


def test_matches_full_relative_path():
    content = '[ext_resource path="res://entities/player.gd" id=1]'
    found = find_text_references(content, "scene.tscn", ["entities/player.gd", "other.gd"])
    assert found == ["entities/player.gd"]


def test_matches_filename_only_without_directory():
    content = "See player.gd for the implementation."
    found = find_text_references(content, "README.md", ["entities/player.gd"])
    assert found == ["entities/player.gd"]


def test_does_not_match_bare_stem():
    # "player" alone (no extension) is common enough prose that matching it
    # would be pure noise -- only filename+extension or the full path count.
    content = "The player controls character movement."
    found = find_text_references(content, "README.md", ["entities/player.gd"])
    assert found == []


def test_does_not_match_a_substring_of_a_longer_token():
    content = "See multiplayer.gd_backup and player.gdx for related work."
    found = find_text_references(content, "README.md", ["player.gd"])
    assert found == []


def test_word_boundary_allows_adjacent_path_separators_and_punctuation():
    content = '"res://player.gd", (player.gd), see player.gd.'
    found = find_text_references(content, "README.md", ["player.gd"])
    assert found == ["player.gd"]


def test_excludes_self_path_even_if_content_mentions_itself():
    content = "config.gd is this very file."
    found = find_text_references(content, "config.gd", ["config.gd", "other.gd"])
    assert found == []


def test_returns_empty_list_when_nothing_matches():
    assert find_text_references("nothing relevant here", "a.md", ["b.gd", "c.gd"]) == []


def test_strict_path_boundary_rejects_a_shorter_path_as_a_suffix_of_a_longer_one():
    # Real bug: "/" was treated as always a harmless boundary character, so
    # a shorter collected file's full relative path could false-match as a
    # substring of a longer, unrelated file's path whenever a "/" sits at
    # the split point -- content naming only "sub/scenes/player.gd" must
    # not also count as a match for the unrelated full path
    # "scenes/player.gd". Tested directly against _contains_token() (not
    # find_text_references()) since a bare filename match -- "player.gd"
    # alone -- legitimately still fires for either candidate regardless of
    # this fix; that's a separate, accepted, documented ambiguity of the
    # filename-only fallback, not what this fix is about.
    content = "Uses sub/scenes/player.gd for this entity."
    assert _contains_token(content, "scenes/player.gd", strict_path_boundary=True) is False


def test_strict_path_boundary_still_matches_a_full_path_preceded_by_a_uri_scheme():
    # A Godot res:// path's double slash must still count as a valid
    # boundary -- the fix above must not overcorrect into rejecting this,
    # the actual common case find_text_references() exists for.
    content = '[ext_resource path="res://scenes/player.gd" id=1]'
    assert _contains_token(content, "scenes/player.gd", strict_path_boundary=True) is True


def test_strict_path_boundary_still_matches_a_full_path_at_the_very_start_of_content():
    assert _contains_token("scenes/player.gd is the entry point.", "scenes/player.gd", strict_path_boundary=True) is True


def test_non_strict_boundary_still_matches_a_filename_preceded_by_any_directory():
    # Unlike the full-path form above (strict_path_boundary=True), a bare
    # filename match is still expected to fire regardless of what directory
    # precedes it -- that's the whole point of the filename-only fallback
    # (a reference that omits the directory), and the default (False) used
    # by find_text_references() for it.
    content = "Uses sub/scenes/player.gd for this entity."
    assert _contains_token(content, "player.gd") is True


def test_matches_multiple_distinct_references():
    content = "Uses both player.gd and enemy.gd for the two characters."
    found = find_text_references(content, "README.md", ["entities/player.gd", "entities/enemy.gd", "unrelated.gd"])
    assert set(found) == {"entities/player.gd", "entities/enemy.gd"}


def test_for_file_reads_and_scans_a_real_text_file(tmp_path):
    (tmp_path / "README.md").write_text("See player.gd for details.", encoding="utf-8")
    found = find_text_references_for_file(str(tmp_path / "README.md"), "README.md", ["player.gd", "other.gd"])
    assert found == ["player.gd"]


def test_for_file_skips_a_file_with_a_tree_sitter_grammar(tmp_path):
    # a .py file's own dependency_handler already covers it -- re-scanning
    # its text (comments, strings) on top would risk noisy incidental
    # matches, so this must return [] without even reading the file's
    # content for reference purposes.
    (tmp_path / "app.py").write_text("# see other.py\n", encoding="utf-8")
    found = find_text_references_for_file(str(tmp_path / "app.py"), "app.py", ["other.py"])
    assert found == []


def test_for_file_returns_empty_list_for_an_unreadable_file(tmp_path):
    missing = tmp_path / "does_not_exist.md"
    assert find_text_references_for_file(str(missing), "does_not_exist.md", ["a.gd"]) == []
