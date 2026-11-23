import pytest
from pydantic import ValidationError

from letssing.playlist import DuplicateError, ItemCreate, ItemPatch, Status


def make(artist="Legião Urbana", title="Tempo Perdido", **kw):
    return ItemCreate(artist=artist, title=title, **kw)


def test_add_sets_folder_and_defaults(repo):
    it = repo.add(make(language="pt"))
    assert it.folder_name == "Legiao Urbana - Tempo Perdido"
    assert it.status == Status.PENDENTE
    assert it.txt_source == "none"
    assert repo.list() == [it]


def test_duplicate_is_rejected(repo):
    first = repo.add(make())
    with pytest.raises(DuplicateError) as e:
        repo.add(make("legiao urbana", "TEMPO PERDIDO"))
    assert e.value.existing.id == first.id
    assert len(repo.list()) == 1


def test_patch_resets_status_and_gap(repo):
    it = repo.add(make())
    repo.set_status(it.id, Status.ERRO, "falhou")
    it = repo.update(it.id, ItemPatch(gap_ms=1250, no_video=True))
    assert it.gap_ms == 1250 and it.no_video
    assert it.status == Status.PENDENTE and it.error_message == ""
    it = repo.update(it.id, ItemPatch(clear_gap=True))
    assert it.gap_ms is None


def test_invalid_url_rejected():
    with pytest.raises(ValidationError):
        make(audio_url="javascript:alert(1)")


def test_needs_review_for_ai(repo):
    it = repo.add(make())
    assert not it.needs_review
    it = repo.set_status(it.id, Status.GERADO_IA)
    assert it.needs_review


def test_delete(repo):
    it = repo.add(make())
    assert repo.delete(it.id)
    assert not repo.delete(it.id)


def test_export_import_roundtrip(repo, tmp_path):
    from letssing.playlist import PlaylistRepository

    repo.add(make(audio_url="https://www.youtube.com/watch?v=abc", gap_ms=500))
    repo.add(make("Queen", "Bohemian Rhapsody", no_video=True))
    data = repo.export()
    assert {"id", "status", "dup_key"}.isdisjoint(data["items"][0])

    other = PlaylistRepository(tmp_path / "other.db")
    r = other.import_(data)
    assert r == {"added": 2, "duplicates": [], "errors": []}
    assert [i.title for i in other.list()] == ["Tempo Perdido", "Bohemian Rhapsody"]
    assert other.list()[0].gap_ms == 500

    r = other.import_(data)  # de novo: tudo duplicado
    assert r["added"] == 0 and len(r["duplicates"]) == 2


def test_import_keeps_going_on_bad_item(repo):
    data = {"format": "letssing-playlist", "version": 1,
            "items": [{"artist": "", "title": "x"}, {"artist": "A", "title": "B"}]}
    r = repo.import_(data)
    assert r["added"] == 1 and len(r["errors"]) == 1


def test_import_rejects_foreign_file(repo):
    with pytest.raises(ValueError):
        repo.import_({"items": []})


# ---------------- API ----------------
def test_api_crud(client):
    r = client.post("/api/playlist", json={"artist": "Legião Urbana", "title": "Tempo Perdido"})
    assert r.status_code == 201
    item = r.json()
    assert item["needs_review"] is False

    r = client.post("/api/playlist", json={"artist": "LEGIAO URBANA", "title": "tempo perdido"})
    assert r.status_code == 409
    assert r.json()["detail"]["existing_id"] == item["id"]

    r = client.patch(f"/api/playlist/{item['id']}", json={"gap_ms": 300})
    assert r.json()["gap_ms"] == 300

    r = client.get("/api/playlist/export")
    assert "attachment" in r.headers["content-disposition"]
    assert len(r.json()["items"]) == 1

    assert client.delete(f"/api/playlist/{item['id']}").status_code == 204
    assert client.get("/api/playlist").json() == []
    assert client.delete(f"/api/playlist/{item['id']}").status_code == 404


def test_api_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "LetsSing Playlist Builder" in r.text
