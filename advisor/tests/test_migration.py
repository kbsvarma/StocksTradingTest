import sqlite3
from pathlib import Path
import pytest
from advisor.ops.migrate import export, restore, SECRETS


def test_wal_database_and_files_restore_without_activation(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'))
    repo=tmp_path/'repo';data=repo/'advisor/data';data.mkdir(parents=True)
    (repo/'advisor/example.py').write_text('code')
    (repo/'advisor/requirements.lock').write_text('requests==2.34.2')
    db=sqlite3.connect(data/'state.sqlite');db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE watchlist(ticker TEXT)');db.execute('INSERT INTO watchlist VALUES (?)',('NVDA',));db.commit()
    bundle=tmp_path/'snapshot.tgz';export(repo,bundle)
    destination=tmp_path/'new-host';result=restore(bundle,destination)
    assert result['services_started'] is False
    assert (destination/'advisor/requirements.lock').read_text()=='requests==2.34.2'
    with sqlite3.connect(destination/'advisor/data/state.sqlite') as restored:
        assert restored.execute('SELECT ticker FROM watchlist').fetchall()==[('NVDA',)]
    assert not (destination/'advisor/data/state.sqlite-wal').exists()
    assert bundle.stat().st_mode & 0o777==0o600
    db.close()
    with pytest.raises(ValueError,match='must not exist'):restore(bundle,destination)
    bundle.write_bytes(bundle.read_bytes()+b'tampered')
    with pytest.raises(ValueError,match='checksum'):restore(bundle,tmp_path/'bad')


def test_configuration_is_excluded_unless_requested(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path/'home'));Path.home().mkdir()
    for name in SECRETS:(Path.home()/name).write_text('TEST=private')
    repo=tmp_path/'repo';(repo/'advisor').mkdir(parents=True);(repo/'advisor/a.py').write_text('x')
    for include in (False,True):
        bundle=tmp_path/f'{include}.tgz';export(repo,bundle,include)
        destination=tmp_path/f'copy{include}';restore(bundle,destination)
        for name in SECRETS:assert (destination/'.advisor-migration/config'/name).exists()==include


def test_provision_restores_model_settings_without_starting_services(tmp_path,monkeypatch):
    from advisor.ops import provision_remote
    home=tmp_path/'home';home.mkdir();monkeypatch.setenv('HOME',str(home))
    repo=tmp_path/'repo';units=repo/'advisor/ops/systemd';units.mkdir(parents=True)
    (units/'advisor-terminal.service').write_text('[Service]\nExecStart=%h/stockstest/.venv/bin/python -m streamlit\n')
    private=repo/'.advisor-migration/config';private.mkdir(parents=True)
    for name in SECRETS:(private/name).write_text('SETTING=test\n')
    calls=[];monkeypatch.setattr(provision_remote.subprocess,'run',lambda args,**kwargs:calls.append(args))
    monkeypatch.setattr('sys.argv',['provision','--repo',str(repo)])
    provision_remote.main()
    assert calls==[['systemctl','--user','daemon-reload']]
    for name in SECRETS:
        assert (home/name).read_text()=='SETTING=test\n'
        assert (home/name).stat().st_mode & 0o777==0o600
