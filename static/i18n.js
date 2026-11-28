/* Языки сайта и игры: русский — исходный, украинский и английский — словари /i18n/<lang>.js.
   Ключ словаря — сама русская фраза (как на странице, пробелы схлопнуты), {} — подставленное значение.
   Переводим готовую страницу: текст узлов, подсказки полей, всплывающие окна. Чего нет в словаре — остаётся по-русски.
   Выбор: ?lang= → сохранённый → уже играл — русский → язык Telegram → язык браузера (украинский, русский и соседние — свои, прочие — английский).
   ponytail: перевод по точному совпадению фразы — новая фраза в коде без строки в словаре останется русской
   (проверка: tests/test_i18n_web.py находит такие фразы). */
(function(){
var L=["ru","uk","en"],NM={ru:"Русский",uk:"Українська",en:"English"},V=window.I18N_V||"";
function pick(){
  var q=(location.search.match(/[?&]lang=(ru|uk|en)\b/)||[])[1];
  try{var s=localStorage.getItem("lang");if(!q&&L.indexOf(s)>=0)return s;
    var old=localStorage.getItem("nunu-prefs")||localStorage.getItem("nunu2")}catch(e){}
  var u=window.Telegram&&Telegram.WebApp&&Telegram.WebApp.initDataUnsafe&&Telegram.WebApp.initDataUnsafe.user,
    c=((u&&u.language_code)||navigator.language||"ru").toLowerCase(),
    l=q||(old?"ru":/^uk/.test(c)?"uk":/^(ru|be|kk|ky|uz|tg|hy|az|ka|tt|ba)/.test(c)?"ru":"en");  /* уже играл по-русски — без сюрприза */
  try{localStorage.setItem("lang",l)}catch(e){}  /* выбор запоминается: дальше язык не прыгает */
  return l;
}
var lang=pick(),D={},P=[],CYR=/[А-Яа-яЁё]/,done=new WeakMap();
var I=window.I18N={lang:lang,names:NM,loc:{ru:"ru-RU",uk:"uk-UA",en:"en-GB"}[lang],
  set:function(l){var ok=true;try{localStorage.setItem("lang",l)}catch(e){ok=false}var u=location.href.replace(/([?&])lang=\w+&?/,"$1").replace(/[?&]$/,"");
    if(!ok)u+=(u.indexOf("?")<0?"?":"&")+"lang="+l;location.href=u},  /* хранилище закрыто — язык едет в адресе */
  t:function(s){var r=tr(String(s));return r==null?s:r},
  /* кнопки выбора языка: <span>RU · UK · EN</span>, нажатие — set() */
  menu:function(cls){return '<span class="'+(cls||"langs")+'" translate="no">'+L.map(function(l){return '<a href="?lang='+l+'" data-lang="'+l+'"'+(l===lang?' aria-current="true"':'')+' title="'+NM[l]+'">'+l.toUpperCase()+'</a>'}).join(" · ")+'</span>'}};
document.documentElement.lang=lang;
document.addEventListener("DOMContentLoaded",function(){[].forEach.call(document.querySelectorAll("[data-lang="+lang+"]"),function(a){a.setAttribute("aria-current","true")})});
document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest("[data-lang]");if(a){e.preventDefault();I.set(a.dataset.lang)}},true);
if(lang==="ru")return;

var norm=function(s){return s.replace(/\s+/g," ").trim()};
var WR=["пн","вт","ср","чт","пт","сб","вс"],MR=["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"],
  WD={en:["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],uk:["пн","вт","ср","чт","пт","сб","нд"]},
  MO={en:["January","February","March","April","May","June","July","August","September","October","November","December"],
      uk:["січня","лютого","березня","квітня","травня","червня","липня","серпня","вересня","жовтня","листопада","грудня"]},
  DT=new RegExp("(?:("+WR.join("|")+"), )?(\\d{1,2}) ("+MR.join("|")+")","g");
/* Порядок: фраза целиком → со строчной буквы (код пишет lc(…)) → шаблон с {} →
   составная («Все: 3», «Сад · лес», несколько предложений подряд) — только если известны все части. */
function tr(s){var k=norm(s);return k&&CYR.test(k)?one(k):null}
function one(k){
  var v=D[k];if(v!=null)return v;
  var u=k.charAt(0).toUpperCase()+k.slice(1);if(u!==k&&(v=D[u])!=null)return v.charAt(0).toLowerCase()+v.slice(1);
  for(var i=0;i<P.length;i++){var p=P[i];if(k.indexOf(p.lit)<0)continue;var m=p.re.exec(k);
    if(m){var j=1;return p.to.replace(/\{\}/g,function(){var g=norm(m[j++]||""),t=CYR.test(g)&&g.length<k.length?one(g):null;return t!=null?t:g})}}
  /* края без букв: «📍 Гараж», «18 ч назад», «так и сделал 84%», «План:» */
  var n=/^([^\p{L}«"]+)(\p{L}.*)$/u.exec(k),t;if(n&&(t=one(norm(n[2])))!=null)return n[1]+t;
  if((n=/^(.*?[\p{L}»"])([^\p{L}»"]+)$/u.exec(k))&&(t=one(norm(n[1])))!=null)return t+n[2];
  /* дата, записанная по-русски в данных игрока: «вт, 24 ноября» */
  var ds=[],r=k.replace(DT,function(_,w,d,m){ds.push((WD[lang][WR.indexOf(w)]?WD[lang][WR.indexOf(w)]+", ":"")+d+" "+MO[lang][MR.indexOf(m)]);return "\u0001"+(ds.length-1)+"\u0001"});
  if(ds.length){t=CYR.test(r)?one(r):r;return t==null?null:t.replace(/\u0001(\d+)\u0001/g,function(_,i){return ds[i]})}
  /* обрезанная «…» фраза: ищем целую и режем перевод до той же длины */
  if(/…$/.test(k)&&k.length>12){var h=k.slice(0,-1).toLowerCase();for(var key in D)if(key.toLowerCase().indexOf(h)===0){t=D[key];return t.slice(0,Math.max(h.length,8)).replace(/\s+\S*$/,"")+"…"}}
  var b=k.split(/((?<=[.!?…]) )/);if(b.length<3)b=k.split(/(: | · | — |, )/);if(b.length<3)return null;  /* сначала предложения, потом части */
  for(i=0;i<b.length;i+=2)if(b[i]&&CYR.test(b[i])){if((v=one(b[i]))==null)return null;b[i]=v}
  return b.join("")}
function text(n){var v=n.data;if(done.get(n)===v)return;var t=tr(v);
  if(t!=null)n.data=(v.match(/^\s*/)[0])+t+(v.match(/\s*$/)[0]);done.set(n,n.data)}
var AT=["placeholder","title","aria-label","alt"],SKIP={SCRIPT:1,STYLE:1,NOSCRIPT:1};
function attrs(el){var d=done.get(el)||{};AT.forEach(function(a){var v=el.getAttribute(a);if(v&&d[a]!==v){var t=tr(v);if(t!=null)el.setAttribute(a,t);d[a]=el.getAttribute(a)}});
  if(el.tagName==="INPUT"&&/^(button|submit)$/.test(el.type)&&el.value&&d.value!==el.value){var t=tr(el.value);if(t!=null)el.value=t;d.value=el.value}
  done.set(el,d)}
function walk(n){
  if(n.nodeType===3)return text(n);
  if(n.nodeType!==1||SKIP[n.tagName]||n.isContentEditable||n.getAttribute("translate")==="no")return;
  attrs(n);if(n.tagName==="TEXTAREA")return;  /* подсказку переводим, написанное человеком — нет */
  for(var c=n.firstChild;c;c=c.nextSibling)walk(c);
}
function off(n){var e=n.nodeType===1?n:n.parentElement;return !e||!!(e.closest&&e.closest('[translate="no"],script,style,[contenteditable="true"]'+(n.nodeType===1?"":",textarea")))}
function run(){
  walk(document.documentElement);
  new MutationObserver(function(ms){ms.forEach(function(m){
    if(m.type==="childList")m.addedNodes.forEach(function(n){if(!off(n))walk(n)});
    else if(!off(m.target))m.type==="characterData"?text(m.target):attrs(m.target)})})
  .observe(document.documentElement,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:AT.concat("value")});
  /* всплывающие окна браузера и Telegram */
  ["alert","confirm","prompt"].forEach(function(f){var o=window[f];if(o)window[f]=function(m){var a=[].slice.call(arguments);a[0]=I.t(m);return o.apply(window,a)}});
  var W=window.Telegram&&Telegram.WebApp;
  if(W){["showAlert","showConfirm"].forEach(function(f){var o=W[f];if(o)W[f]=function(m){var a=[].slice.call(arguments);a[0]=I.t(m);return o.apply(W,a)}});
    var sp=W.showPopup;if(sp)W.showPopup=function(p,cb){p=Object.assign({},p);if(p.title)p.title=I.t(p.title);if(p.message)p.message=I.t(p.message);
      if(p.buttons)p.buttons=p.buttons.map(function(b){return b.text?Object.assign({},b,{text:I.t(b.text)}):b});return sp.call(W,p,cb)}}
}
window.I18N_SET=function(d){D=d;
  /* «· нажми, чтобы изменить» в коде клеится к значению: та же фраза без краевых « · », «: », «(» */
  for(var a in d){var c=a.replace(/^(\d+\. )?[\s·:,—(]*|[\s·:,—(«]+$/g,"");if(c!==a&&c.indexOf("{}")<0&&CYR.test(c)&&!(c in d))d[c]=d[a].replace(/^(\d+\. )?[\s·:,—(]*|[\s·:,—(«]+$/g,"")}for(var k in d)if(k.indexOf("{}")>=0){var parts=k.split("{}"),lit=parts.reduce(function(a,b){return b.length>a.length?b:a},"");
    /* {} не режет слово: «{} м» не поймает «в море» */
    var re=parts.map(function(x,i){var e=x.replace(/[.*+?^${}()|[\]\\]/g,"\\$&");
      return (i&&/^\p{L}/u.test(x)?"(?<!\\p{L})":"")+e+(i<parts.length-1&&/\p{L}$/u.test(x)?"(?!\\p{L})":"")}).join("(.*?)");
    P.push({lit:lit,re:new RegExp("^"+re+"$","u"),to:d[k],w:k.length-2*(parts.length-1)})}
  P.sort(function(a,b){return b.w-a.w});run()};
document.write('<script src="/i18n/'+lang+'.js?v='+V+'"><\/script>');
})();
