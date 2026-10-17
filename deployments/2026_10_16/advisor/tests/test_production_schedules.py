from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_friday_close_research_and_publication_are_scheduled():
    research=(ROOT/'ops/systemd/advisor-research.timer').read_text()
    brief=(ROOT/'ops/systemd/advisor-brief.timer').read_text()
    assert 'OnCalendar=Fri 17:15 America/New_York' in research
    assert 'OnCalendar=Fri 18:00 America/New_York' in brief


def test_deployment_enables_continuous_reassessment():
    deploy=(ROOT/'ops/deploy_linux.sh').read_text()
    assert 'enable --now advisor-intelligence.timer' in deploy
