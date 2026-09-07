from advisor.investigator.reasoner import revision_context,authored_context


def test_revision_keeps_authored_claims_sources_and_all_failed_checks_without_duplication():
    proposal={'insights':[{'id':'a','mechanism':'An authored inference','what_changed':'Computed growth',
        'what_is_priced_in':'Computed sensitivity','evidence':[{'source_id':'source','excerpt':'Exact source quote'}]}]}
    feedback={'review':{'insights':[{'id':'a','supported':False,'reason':'The inference confuses gain and adjustment.'},
        {'id':'b','supported':True,'reason':'Supported.'}],'action_supported':False,'missed_questions':['Check taxes.']},
        'checks':[{'id':'a','reasons':['The inference confuses gain and adjustment.']},
                  {'id':'conditions','reasons':['Unknown cutoff.']}]}
    draft,result=revision_context(proposal,feedback)
    assert draft['insights'][0]['mechanism']==proposal['insights'][0]['mechanism']
    assert draft['insights'][0]['evidence']==proposal['insights'][0]['evidence']
    assert result['review']['insights'][0]['reason']==feedback['review']['insights'][0]['reason']
    assert result['review']['missed_questions']==['Check taxes.']
    assert result['checks']==[{'id':'conditions','reasons':['Unknown cutoff.']}]
    assert 'what_changed' in proposal['insights'][0] and len(feedback['review']['insights'])==2


def test_only_application_marked_numeric_citations_are_omitted_from_model_context():
    rows=[{'id':'computed','kind':'fundamental'},{'id':'authored','kind':'fundamental'},
          {'id':'release','kind':'document'}]
    citations=[{'source_id':x['id'],'excerpt':'Original evidence','use':'current'} for x in rows]
    proposal={'insights':[{'id':'a','evidence':citations,'_computed_citation_ids':['computed','release'],
                          '_computed_finding_ids':['cash_conversion'],'mechanism':'An authored inference',
                          'what_changed':'Source-bound calculated observation'}]}
    context=authored_context(proposal,rows)
    assert [c['source_id'] for c in context['insights'][0]['evidence']]==['authored','release']
    assert context['insights'][0]['mechanism']==proposal['insights'][0]['mechanism']
    assert context['insights'][0]['finding_ids']==['cash_conversion']
    assert 'what_changed' not in context['insights'][0]
    assert len(proposal['insights'][0]['evidence'])==3
