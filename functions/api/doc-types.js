// Doc types are static — no DB needed
const DOC_TYPES = {
  mutual_termination: { title:'协商解除劳动合同协议书', desc:'双方协商一致解除，约定补偿与交接',
    fields:[{key:'company',label:'公司名称',type:'text',ph:'',req:true},{key:'employee',label:'员工姓名',type:'text',ph:'',req:true},
      {key:'position',label:'岗位',type:'text',ph:'',req:false},{key:'hire_date',label:'入职日期',type:'date',ph:'',req:false},
      {key:'term_date',label:'协商离职日期',type:'date',ph:'',req:true},
      {key:'compensation',label:'经济补偿金额（元）',type:'number',ph:'',req:false},
      {key:'note',label:'其他约定 / 情况说明',type:'textarea',ph:'如：年假已休完、有竞业限制约定…',req:false}] },
  dismissal_notice: { title:'解除劳动合同通知书', desc:'单方解除（过失性 / 无过失性）',
    fields:[{key:'company',label:'公司名称',type:'text',ph:'',req:true},{key:'employee',label:'员工姓名',type:'text',ph:'',req:true},
      {key:'position',label:'岗位',type:'text',ph:'',req:false},
      {key:'basis',label:'解除事由与依据',type:'textarea',ph:'如：连续旷工 5 个工作日，违反员工手册第X条…',req:true},
      {key:'term_date',label:'解除日期',type:'date',ph:'',req:true},{key:'note',label:'交接与结算安排',type:'textarea',ph:'',req:false}] },
  warning_letter: { title:'违纪警告处分通知书', desc:'违纪行为的书面警告与整改要求',
    fields:[{key:'company',label:'公司名称',type:'text',ph:'',req:true},{key:'employee',label:'员工姓名',type:'text',ph:'',req:true},
      {key:'department',label:'部门',type:'text',ph:'',req:false},
      {key:'fact',label:'违纪事实',type:'textarea',ph:'时间、地点、具体行为、证据…',req:true},
      {key:'rule',label:'违反的制度条款',type:'text',ph:'如：《员工手册》第 5.2 条',req:false},
      {key:'demand',label:'整改要求',type:'textarea',ph:'',req:false}] },
  return_to_work: { title:'催告返岗通知书', desc:'旷工 / 失联员工的限期返岗催告',
    fields:[{key:'company',label:'公司名称',type:'text',ph:'',req:true},{key:'employee',label:'员工姓名',type:'text',ph:'',req:true},
      {key:'absent_from',label:'旷工起始日期',type:'date',ph:'',req:true},
      {key:'deadline',label:'限期返岗日期',type:'date',ph:'',req:true},
      {key:'note',label:'补充说明',type:'textarea',ph:'联系方式、已尝试的联系记录…',req:false}] },
  transfer_notice: { title:'调岗通知书', desc:'工作岗位调整的书面通知',
    fields:[{key:'company',label:'公司名称',type:'text',ph:'',req:true},{key:'employee',label:'员工姓名',type:'text',ph:'',req:true},
      {key:'old_position',label:'原岗位',type:'text',ph:'',req:true},{key:'new_position',label:'新岗位',type:'text',ph:'',req:true},
      {key:'reason',label:'调岗理由',type:'textarea',ph:'组织架构调整 / 岗位撤销 / 身体原因…',req:true},
      {key:'effective',label:'生效日期',type:'date',ph:'',req:false},{key:'salary',label:'薪酬变化说明',type:'text',ph:'如：薪酬标准不变',req:false}] },
  probation_fail: { title:'试用期不符合录用条件通知书', desc:'试用期解除（第三十九条第一项）',
    fields:[{key:'company',label:'公司名称',type:'text',ph:'',req:true},{key:'employee',label:'员工姓名',type:'text',ph:'',req:true},
      {key:'position',label:'岗位',type:'text',ph:'',req:true},{key:'hire_date',label:'入职日期',type:'date',ph:'',req:false},
      {key:'probation_end',label:'试用期截止日期',type:'date',ph:'',req:true},
      {key:'fact',label:'考核情况与不符合录用条件的事实',type:'textarea',ph:'',req:true}] },
};

export async function onRequestGet() {
  const list = Object.entries(DOC_TYPES).map(([k,v]) => ({ key:k, title:v.title, desc:v.desc, fields:v.fields }));
  return new Response(JSON.stringify(list), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' }
  });
}

export async function onRequestOptions() {
  return new Response(null, { status:204, headers: { 'Access-Control-Allow-Origin':'*',
    'Access-Control-Allow-Methods':'GET,OPTIONS', 'Access-Control-Allow-Headers':'Content-Type' } });
}
