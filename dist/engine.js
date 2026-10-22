/* Deterministic prototype rules. No model calls or external integrations. */
(function(root){
const weights={B:25,A:20,N:25,T:15,H:5,E:10};
const labels={B:'Budget',A:'Authority',N:'Need',T:'Timeline',H:'History',E:'Engagement'};
const anchors={
B:['Confirmed no viable funding path','Interest, but no credible funding path identified','Possible funding source identified','Funding source and approval process understood; approval pending','Budget approved and covers the initial scope','Funding, scope and spending authority confirmed'],
A:['Confirmed no viable decision-access path','Limited influence; no introduction path','Willing to sponsor or introduce; path not established','Sponsor and decision process identified','Key decision-maker involved','Final decision-maker and procurement path confirmed'],
N:['No relevant need or confirmed service mismatch','General interest without a specific problem','Specific problem; impact or fit needs validation','Specific problem, business impact and initial CGS fit','Impact, objectives and support scope clear','Need, success measures and priority jointly confirmed'],
T:['Project cancelled or confirmed no action plan','Long-term interest without a trigger','Action window or business trigger identified','Target date and milestones identified','Timeline, dependencies and plan clear','Near-term evaluation and decision plan agreed'],
H:['Material, relevant unresolved relationship issue','Historical issues partially resolved','Limited relevance or mixed experience','Positive previous collaboration','Relevant successful project and ongoing relationship','Repeated relevant success and explicit endorsement'],
E:['Client declined further progress','Courtesy reply or passive receipt','Substantive discussion and openness to next actions','One useful follow-up action completed','Consistent information-sharing or coordination','Multiple stakeholders actively advancing the opportunity']};
const conditionLabels={problem:'Specific client problem',fit:'CGS service fit',external:'Openness to external support',next:'Agreed next step',commercial:'Funding and decision-access path',conflict:'Material evidence conflicts resolved'};
const reasons={NO_EXTERNAL_SUPPORT:'Not considering external support',SERVICE_MISMATCH:'Need does not fit CGS services',FUNDING_UNAVAILABLE:'No viable funding path',PROJECT_DEFERRED:'Project deferred',PROJECT_CANCELLED:'Project cancelled',NO_DECISION_ACCESS:'No viable decision-access path',CLIENT_DECLINED:'Client declined further progress'};
function tier(n){return n>=1000000?5:n>=500000?4:n>=100000?3:2}
function band(n){return n>=75?'High':n>=55?'Medium':'Low'}
function calc(c){let ql=0,coverage=0;for(const k of Object.keys(weights)){const s=c.scores[k];if(s!==null&&s!==undefined){ql+=weights[k]*s/5;coverage+=weights[k]}}
const qu=ql+100-coverage;let rl=0,ru=100,rt=null;
if(c.revenue){rl=tier(c.revenue.low)*20;ru=tier(c.revenue.high)*20;rt=rl===ru?rl/20:null}
const pl=.6*ql+.4*rl,pu=.6*qu+.4*ru;
const floors=Object.entries({B:2,A:2,N:3,T:2,E:2}).every(([k,v])=>c.scores[k]!=null&&c.scores[k]>=v);
const all=Object.values(c.conditions).every(x=>x===true);
const readiness=c.blocker?'Not ready':all&&coverage>=80&&ql>=70&&floors?'Ready':'More evidence needed';
const priority=!c.revenue?'Pending confirmation':band(pl)===band(pu)?band(pl):`${band(pl)}–${band(pu)}`;
return {ql,qu,coverage,rl,ru,rt,pl,pu,readiness,priority};}
root.CGS={weights,labels,anchors,conditionLabels,reasons,tier,band,calc};
if(typeof module!=='undefined')module.exports=root.CGS;
})(typeof window!=='undefined'?window:globalThis);
