from ziplex.text_references import (
    find_text_references, find_text_references_for_file, merge_text_references, _contains_token,
)


def test_merge_text_references_folds_matches_into_dependencies_and_records_them_separately():
    # The shared merge step packager.py's per-file loop and cli.py's `tree`
    # subcommand both route through -- see this module's own docstring for
    # why this used to be independently reimplemented at each call site.
    deps, text_deps = merge_text_references(["a.py"], ["readme.md"])
    assert deps == ["a.py", "readme.md"]
    assert text_deps == ["readme.md"]


def test_merge_text_references_is_a_plain_concatenation_not_a_dedup():
    # packager.py's caller can re-run this merge against an already-merged
    # `dependencies` restored from a checkpoint -- a harmless duplicate here
    # is fine, since build_tree() already dedupes when building internal/
    # external.
    deps, text_deps = merge_text_references(["a.py", "readme.md"], ["readme.md"])
    assert deps == ["a.py", "readme.md", "readme.md"]
    assert text_deps == ["readme.md"]


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
    # find_text_references()) since this fix is specifically about the
    # full-path form -- the bare filename form's own basename-ambiguity
    # handling is a separate fix, tested separately below.
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


def test_strict_path_boundary_matches_a_path_preceded_by_a_relative_link_prefix():
    # Real gap: a full relative path immediately after a single "./" or
    # "../" (the common way a Markdown link or config value spells a
    # relative path) used to never match -- the char right before the path
    # is always just a bare "/", which the no-prefix alternative excludes
    # and the URI-scheme alternative requires a doubled "//" for instead.
    assert _contains_token("see ./scenes/player.gd for details", "scenes/player.gd", strict_path_boundary=True) is True
    assert _contains_token("see ../scenes/player.gd for details", "scenes/player.gd", strict_path_boundary=True) is True
    assert _contains_token("../../scenes/player.gd", "scenes/player.gd", strict_path_boundary=True) is True


def test_strict_path_boundary_relative_link_prefix_still_rejects_a_longer_word():
    # The relative-link fix above must not overcorrect into treating any
    # "/" as a valid boundary again -- "foo../scenes/player.gd" isn't a
    # real relative path (the ".." isn't its own path segment), so it must
    # still be rejected the same way the longer-path-suffix case is.
    assert _contains_token("foo../scenes/player.gd", "scenes/player.gd", strict_path_boundary=True) is False


def test_non_strict_boundary_still_matches_a_filename_preceded_by_any_directory():
    # Unlike the full-path form above (strict_path_boundary=True), a bare
    # filename match is still expected to fire regardless of what directory
    # precedes it -- that's the whole point of the filename-only fallback
    # (a reference that omits the directory), and the default (False) used
    # by find_text_references() for it.
    content = "Uses sub/scenes/player.gd for this entity."
    assert _contains_token(content, "player.gd") is True


def test_bare_filename_match_skipped_when_two_other_files_share_the_basename():
    # Real bug found dogfooding Ziplex on its own repo: a single mention of
    # "AGENTS.md" (no directory) used to link to *every* nested AGENTS.md in
    # the project, not just the one actually meant -- an ambiguous mention
    # should match neither candidate rather than guess at all of them, the
    # same "ambiguous is worse than missed" call already made for bare
    # stems. Neither candidate here is root-level (both have a directory
    # prefix), so the full-path branch can't disambiguate either -- that
    # case (a mention matching one candidate's own exact full path) is
    # covered separately below.
    content = "See AGENTS.md for the full pipeline overview."
    found = find_text_references(content, "workflow.yml", ["src/AGENTS.md", "docs/AGENTS.md"])
    assert found == []


def test_bare_filename_ambiguity_does_not_suppress_an_exact_root_level_match():
    # A root-level file's own full relative path *is* its bare filename
    # (no directory component) -- a mention of "AGENTS.md" is a full,
    # unambiguous match for a root-level AGENTS.md regardless of how many
    # other, differently-pathed files elsewhere share that basename. This
    # is the actual real-world shape of the bug above: "the root AGENTS.md"
    # correctly resolves to the root file (an exact full-path match) while
    # no longer also fanning out to unrelated nested AGENTS.md files.
    content = "See the root AGENTS.md for the full pipeline overview."
    found = find_text_references(content, "workflow.yml", ["AGENTS.md", "src/AGENTS.md"])
    assert found == ["AGENTS.md"]


def test_bare_filename_match_still_fires_when_only_one_other_file_has_that_basename():
    # The fix above must not regress the common, unambiguous case -- a
    # single project-wide match for a basename should still resolve.
    content = "See player.gd for the implementation."
    found = find_text_references(content, "README.md", ["entities/player.gd", "entities/enemy.gd"])
    assert found == ["entities/player.gd"]


def test_full_path_match_unaffected_by_a_shared_basename_elsewhere():
    # A full relative path always names one specific candidate -- ambiguity
    # in the *bare* filename fallback for other same-named files elsewhere
    # must not suppress this unambiguous form.
    content = 'Uses "res://src/AGENTS.md" specifically, not the root one.'
    found = find_text_references(content, "workflow.yml", ["AGENTS.md", "src/AGENTS.md"])
    assert found == ["src/AGENTS.md"]


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
