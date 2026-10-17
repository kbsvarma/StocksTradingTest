from advisor.investigator.disclosure_checks import errors


def test_recent_commentary_cannot_renew_an_old_quarter_concentration_number():
    rows={'news':{'kind':'document','authority':'reported','temporal':{'state':'current'},
                  'payload':{'dimension':'fundamentals','text':'A customer represents 45% of backlog.'}},
          'old':{'kind':'document','authority':'primary','temporal':{'state':'context_only'},
                 'payload':{'text':'A customer represents 45% of backlog.'}},
          'new':{'kind':'document','authority':'issuer_statement','temporal':{'state':'current'},
                 'payload':{'text':'Current backlog grew 84%.'}}}
    citations=[{'source_id':key,'use':'historical_comparison' if key=='old' else 'current',
                'excerpt':row['payload']['text']} for key,row in rows.items()]
    assert errors({'evidence':citations},rows)
    rows['new']['payload']['text']+=' The customer still represents 45.0 percent.'
    assert not errors({'evidence':citations},rows)


def test_amount_units_normalize_but_estimates_are_not_reported_company_accounts():
    rows={'a':{'kind':'document','authority':'reported','temporal':{'state':'current'},
               'payload':{'dimension':'accounting'}},
          'b':{'kind':'document','authority':'primary','temporal':{'state':'current'},
               'payload':{'text':'The gain was 3,200 million dollars.'}}}
    citations=[{'source_id':'a','use':'current','excerpt':'A gain of 3.2 billion dollars.'},
               {'source_id':'b','use':'current','excerpt':'The gain was 3,200 million dollars.'}]
    assert not errors({'evidence':citations},rows)
    rows['b']['payload']['text']='The gain was 3,100 million dollars.'
    assert errors({'evidence':citations},rows)
    rows['a']['payload']['dimension']='estimates'
    assert not errors({'evidence':citations},rows)
