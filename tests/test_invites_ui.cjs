const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('web/index.html','utf8');
const source=html.slice(html.indexOf('function clearInvite()'),html.indexOf('async function loadMembers'));
const nodes={'#invite-link':{value:''},'#invite-created':{hidden:true},'#dlg-settings':{open:true}};
let resolve, payload, path;
const context=vm.createContext({$:id=>nodes[id],showToast:()=>{},loadMembers:async()=>{},api:(p,b)=>{path=p;payload=b;return new Promise(r=>resolve=r)}});
vm.runInContext(source,context);
(async()=>{
 // Enrollment mints a signup link; it does not pre-create an empty member.
 let task=vm.runInContext('adminSignupInvite()',context);
 assert.equal(path,'/api/admin/signup-invite');assert.deepEqual(payload,{});
 resolve({url:'https://example/signup#invite=secret'});await task;
 assert.equal(nodes['#invite-created'].hidden,false);assert(nodes['#invite-link'].value.includes('#invite='));
 vm.runInContext('clearInvite()',context);assert.equal(nodes['#invite-link'].value,'');
 task=vm.runInContext("invitePerson('existing')",context);assert.equal(payload.id,'existing');
 vm.runInContext('clearInvite()',context);nodes['#dlg-settings'].open=false;
 resolve({invite:'late-secret'});await task;assert.equal(nodes['#invite-link'].value,'');
 assert(!html.includes('Create person and invite'));
 const link=fs.readFileSync('web/link.html','utf8');assert(link.indexOf('history.replaceState')<link.indexOf('fetch("/api/auth/session"'));
 assert(!link.includes('Authorization'));assert(!html.includes('viewer.email'));
 console.log('Invite DOM PASS: admin signup links, one-time display, wipe, late-response suppression, fragment exchange, display name');
})().catch(e=>{console.error(e);process.exit(1)});