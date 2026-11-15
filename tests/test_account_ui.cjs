const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('web/index.html','utf8');
const source=html.slice(html.indexOf('const AUTH_METHOD_LABEL'),html.indexOf('function inviteExpiry'));
const ids=['#account-email','#account-method','#account-current','#account-current-label','#account-emailnew',
  '#account-emailnew-label','#account-providers','#account-provider-list','#account-status',
  '#account-new','#account-confirm'];
const nodes={};ids.forEach(id=>nodes[id]={value:'',textContent:'',hidden:false});
let body=null;
const context=vm.createContext({
  $:id=>nodes[id],showToast:()=>{},
  api:async(p,b)=>{assert.equal(p,'/api/account/password');body=b;return {ok:true}},
  fetch:async()=>({json:async()=>({email:'m@example.com',has_password:true,auth_method:'login_link',google:false,apple:false})}),
});
vm.runInContext(source,context);
const fill=a=>vm.runInContext(`fillAccount(${JSON.stringify(a)})`,context);
(async()=>{
 // A login-link member with no password yet: no current password, but a login address is needed.
 fill({email:'',has_password:false,auth_method:'login_link',google:false,apple:false});
 assert.equal(nodes['#account-current'].hidden,true);
 assert.equal(nodes['#account-emailnew'].hidden,false);
 assert(nodes['#account-method'].textContent.includes('login link'));
 nodes['#account-new'].value='another good one';
 nodes['#account-confirm'].value='another good one';
 nodes['#account-emailnew'].value='m@example.com';
 await vm.runInContext('changeAccountPassword()',context);
 assert.deepEqual(body,{new_password:'another good one',confirm_password:'another good one',email:'m@example.com'});

 // A password member must confirm the current one, and the login address is not editable here.
 fill({email:'m@example.com',has_password:true,auth_method:'password',google:true,apple:false});
 assert.equal(nodes['#account-current'].hidden,false);
 assert.equal(nodes['#account-emailnew'].hidden,true);
 assert.equal(nodes['#account-providers'].hidden,false);
 assert.equal(nodes['#account-provider-list'].textContent,'Google');
 nodes['#account-current'].value='correct horse battery';
 nodes['#account-new'].value='another good one';
 nodes['#account-confirm'].value='another good one';
 await vm.runInContext('changeAccountPassword()',context);
 assert.equal(body.current_password,'correct horse battery');
 assert.equal(body.email,undefined);
 assert(nodes['#account-status'].textContent.startsWith('Password changed'));
 console.log('Account DOM PASS: method wording, current/email fields, providers, payload shape');
})().catch(e=>{console.error(e);process.exit(1)});