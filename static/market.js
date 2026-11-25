'use strict';

window.GgselMarket = function ({PG,A,api,go,render,sheet,closeSheet,refreshMe,getMe,esc,ico,busy,currencyIcon,currencies}) {
  const I = window.GgselI18n;
  const t = (...args) => I.t(...args);
  const defaults = {q:'',currency:'',sort:'archive',page:1,collection:'',model:'',backdrop:'',symbol:'',min:'',max:''};
  const filters = {...defaults};
  const basket = new Map(), shown = new Map(), favorites = new Set(), previewCache = new Map();
  const extraTraits = new Set(['backdrop','symbol']);
  let checkoutView, marketRequest=0, favoritesOnly=false, source='catalog', observer, pickerObserver, picker, extraDraft, extraOpener;
  const offers = new Map(), owned = new Map();
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const sortOptions = {archive:'marketSortDefault',low:'marketSortLow',high:'marketSortHigh'};
  const currencyNames = {RUB:'₽',UAH:'₴',KZT:'₸',UZS:'UZS',TON:'GRAM',STARS:'Stars'};
  const currencyLabels = {RUB:'currencyRub',UAH:'currencyUah',KZT:'currencyKzt',UZS:'currencyUzs',BYN:'currencyByn',STARS:'stars',TON:'marketTon',USDT:'usdt',BTC:'btc'};
  const previewLabels = {collection:'marketGifts',model:'marketModels',backdrop:'marketBackdrop',symbol:'marketSymbol'};
  const clearLabels = {collection:'marketAllGifts',model:'marketAllModels'};
  const unit = currency => currencyNames[currency] || currency;
  const number = (n,digits=0) => new Intl.NumberFormat(I.locale,{maximumFractionDigits:digits}).format(n);
  const amount = item => number(item.price,{STARS:0,TON:8,USDT:6,BTC:8}[item.currency] ?? 2);
  const withdrawButton = () => `<button type="button" class="btn s sm" data-act="marketWithdraw">${ico('out')}${t('marketWithdraw')}</button>`;

  function image(item, eager=false) {
    return `<span class="market-image" ${item.animated ? `data-market-motion="${item.image_id}"` : ''}>${item.image_id
      ? `<img src="${esc(item.image_url||`/static/market-art/${item.image_id}`)}" width="320" height="320" ${eager ? '' : 'loading="lazy"'} decoding="async" alt="">`
      : ico('gift')}</span>`;
  }

  function price(item,compact=false) {
    return `<span class="market-price" aria-label="${amount(item)} ${unit(item.currency)}">${currencyIcon(item.currency)}<strong>${amount(item)}${compact?'':' '+unit(item.currency)}</strong></span>`;
  }

  function card(item,index) {
    const itemAction = item.offer_id ? 'marketOfferDetail' : 'marketOpen';
    const itemId = item.offer_id || item.id;
    return `<article class="market-card">
      <button class="market-card-open" aria-label="${esc(item.name)} #${item.number}" data-act="${itemAction}" data-arg="${itemId}">${image(item,index<4)}
        <span class="market-card-copy"><strong class="market-name" data-user-text>${esc(item.name)}</strong>
          <span class="market-serial" data-user-text>#${item.number}</span></span>
      </button>
      <div class="market-card-actions">${item.offer_id?`<button class="market-add" data-act="marketOffer" data-arg="${itemId}">${t('marketBuy')}</button>`:
        `<button class="market-card-buy" data-act="marketBuyOne" data-arg="${item.id}" aria-label="${t('marketBuy')} ${esc(item.name)}">${price(item,true)}</button>
        <button class="market-add market-add-icon" aria-label="${t(basket.has(item.id)?'marketInBasket':'marketAdd')}" aria-pressed="${basket.has(item.id)}" data-act="marketAdd" data-arg="${item.id}">${ico(basket.has(item.id)?'check':'cart')}</button>`}
      </div>
    </article>`;
  }

  function control(action,arg,label,value,disabled=false,icon='') {
    return `<button type="button" class="market-control" data-act="${action}" data-arg="${arg}" aria-label="${t(label)}" ${disabled?'disabled':''}>
      ${icon}<span><small>${t(label)}</small><b data-user-text>${esc(value)}</b></span>${ico('chev')}
    </button>`;
  }

  function filterModal(html,opener) {
    const modal=sheet(html,opener);
    modal.classList.add('market-filter-overlay');
    return modal;
  }

  async function previews(kind) {
    const collection = kind==='collection' ? '' : filters.collection;
    const key = JSON.stringify([kind,collection]);
    if (!previewCache.has(key)) {
      const values = await api('/market/previews/' + kind + '?' + new URLSearchParams({collection}));
      previewCache.set(key,values);
    }
    return previewCache.get(key);
  }

  function pagination(page,pages) {
    if (pages < 2) return '';
    return `<nav class="market-pagination" aria-label="${t('marketPages')}">
      <button class="btn s sm" data-act="marketPage" data-arg="${page-1}" ${page===1?'disabled':''}>${t('back')}</button>
      <span>${t('marketPageNumber',{page:number(page),pages:number(pages)})}</span>
      <button class="btn s sm" data-act="marketPage" data-arg="${page+1}" ${page===pages?'disabled':''}>${t('marketNext')}</button>
    </nav>`;
  }

  PG.market = async () => {
    const request=++marketRequest;
    const params = new URLSearchParams(filters);
    params.set('source',source);
    if (favoritesOnly) params.set('ids',[...favorites].join(','));
    const result = await api('/market/listings?' + params);
    if (request!==marketRequest) return '';
    shown.clear(); offers.clear();
    for (const item of result.items) {
      if (item.offer_id) offers.set(item.offer_id,item);
      else shown.set(item.id,item);
    }
    const activeFilters=Boolean(filters.q||filters.collection||filters.model||filters.backdrop||filters.symbol||filters.min||filters.max||filters.currency||favoritesOnly);
    return `<section class="market-page">
      <button class="gates-promo market-banner" data-act="new" aria-label="${t('gatesPromoTitle')}"><video src="/static/assets/gates_market_${I.lang}.mp4" poster="/static/assets/gates_market_${I.lang}.webp" muted autoplay loop playsinline preload="metadata" aria-hidden="true"></video></button>
      <div class="gates-market-tabs" role="group" aria-label="${t('marketTitle')}"><button class="active" data-act="marketClose">${t('gatesGifts')}</button><button data-act="marketMine">${t('marketMine')}</button><button data-act="tab" data-arg="deals">${t('gatesDeals')}</button></div>
      <div class="market-search-row"><form class="market-search" data-market-search role="search">
        <button type="submit" aria-label="${t('search')}">${ico('search')}</button><input name="q" value="${esc(filters.q)}" type="search" aria-label="${t('marketSearch')}"
          placeholder="${t('marketSearch')}" maxlength="120" autocomplete="off" enterkeyhint="search">
      </form><button class="market-cart-square" data-act="marketBasket" aria-label="${t('marketBasket')}">${ico('cart')}<b data-market-basket-count ${basket.size?'':'hidden'}>${basket.size}</b></button></div>
      <div class="market-pickers">
        ${control('marketVisual','collection','marketGifts',filters.collection||t('marketAllGifts'))}
        ${control('marketVisual','model','marketModels',filters.model||t('marketAllModels'),!filters.collection)}
        ${control('marketVisual','backdrop','marketBackdrop',filters.backdrop||t('marketAny'))}
        <button class="market-filter-square" type="button" data-act="marketFilters" aria-label="${t('marketFilters')}" aria-pressed="${Boolean(filters.backdrop||filters.symbol||filters.min||filters.max)}">${ico('filter')}</button>
      </div>
      <div class="market-toolbar">
        <label class="market-sort-label">${ico('sort')}<select aria-label="${t('marketSort')}" id="market-sort"><option value="archive" ${filters.sort==='archive'?'selected':''}>${t('marketSort')}</option>${Object.entries(sortOptions).filter(([value])=>value!=='archive').map(([value,label])=>
          `<option value="${value}" ${value===filters.sort?'selected':''}>${t(label)}</option>`).join('')}</select>
        </label><button class="market-currency-short" data-act="marketCurrencyMenu">${filters.currency?esc(unit(filters.currency)):t('currency')}${ico('chev')}</button>${activeFilters?`<button class="market-reset" data-act="marketReset">${t('marketReset')}</button>`:''}
      </div>
      ${result.items.length ? `<div class="market-grid">${result.items.map(card).join('')}</div>` : `<div class="empty">${t('marketNoGifts')}</div>`}
      ${pagination(result.page,result.pages)}
    </section>`;
  };

  function giftDetails(item) {
    const traits = [['marketModel',item.model],['marketBackdrop',item.backdrop],['marketSymbol',item.symbol]];
    return `<section class="market-page market-detail">
      <button class="back" data-act="back">${ico('back')}${t('back')}</button>
      <h1 data-user-text>${esc(item.name)}</h1>
      <p class="market-serial" data-user-text>#${number(item.number)}</p>
      ${image(item,true)}
      <div class="card market-detail-price"><div>${t(item.offer_id?'marketSalePrice':'marketCatalogPrice')}${price(item)}</div></div>
      <div class="market-detail-actions">
        <button class="btn" data-act="${item.offer_id?'marketOffer':'marketBuyOne'}" data-arg="${item.offer_id||item.id}">${t('marketBuy')}</button>
        ${item.offer_id?'':`<button class="btn s" aria-pressed="${basket.has(item.id)}" data-act="marketAdd" data-arg="${item.id}">${t(basket.has(item.id)?'marketInBasket':'marketAdd')}</button>`}
      </div>
      <dl class="card market-traits">${traits.filter(([,value])=>value).map(([key,value])=>
        `<div><dt>${t(key)}</dt><dd data-user-text>${esc(value)}</dd></div>`).join('')}</dl>
    </section>`;
  }

  PG['market-gift'] = async (id) => {
    const item = await api('/market/listings/' + id);
    shown.set(item.id,item);
    return giftDetails(item);
  };
  PG['market-offer'] = item => giftDetails(item);

  PG['market-owned'] = async () => {
    const items = await api('/market/mine');
    owned.clear();
    for (const item of items) owned.set(item.id,item);
    return `<section class="market-page market-detail"><button class="back" data-act="back">${ico('back')}${t('back')}</button>
      <header class="market-header market-withdraw-header"><h1>${t('marketMine')}</h1>${withdrawButton()}</header>
      ${items.length?`<div class="market-grid">${items.map(item=>`<article class="market-card market-owned-card">
        ${image(item)}<span class="market-card-copy"><strong class="market-name" data-user-text>${esc(item.name)}</strong>
        <span class="market-serial" data-user-text>#${number(item.number)}</span>${price(item.offer_id?{currency:item.offer_currency,price:item.offer_price}:item)}</span>
        <div class="market-owned-actions">${item.offer_id?`<span class="market-sale-state">${t('marketListed')}</span>
          <button class="btn s sm" data-act="marketListGift" data-arg="${item.id}">${t('marketEditOffer')}</button>
          <button class="text-button" data-act="marketCancelOffer" data-arg="${item.offer_id}">${t('marketCancelOffer')}</button>`:
          `<button class="btn s sm" data-act="marketListGift" data-arg="${item.id}">${t('marketListGift')}</button>`}</div></article>`).join('')}</div>`:`<div class="empty">${t('marketMineEmpty')}</div>`}
    </section>`;
  };

  const decimalUnits = value => {
    const [whole,fraction=''] = String(value).split('.');
    return BigInt(whole + fraction.padEnd(8,'0'));
  };
  const decimalFromUnits = units => {
    const digits = String(units).padStart(9,'0');
    return digits.slice(0,-8)+'.'+digits.slice(-8);
  };

  function basketTotals(items) {
    const totals = new Map();
    for (const item of items) {
      totals.set(item.currency,(totals.get(item.currency)||0n) + decimalUnits(item.price));
    }
    return [...totals].map(([currency,units])=>({currency,units,price:decimalFromUnits(units)}));
  }

  function basketSheet(checkout,opener,items=[...basket.values()]) {
    const totals = basketTotals(items);
    const balances = getMe()?.balances;
    const unavailable = new Set(checkout&&balances?totals.filter(item=>item.units>decimalUnits(balances[item.currency]||0)).map(item=>item.currency):[]);
    const single = checkout&&items.length===1;
    const body = items.map(item=>`<div class="market-basket-row">
      ${image({...item,animated:false},true)}<div><b data-user-text>${esc(item.name)}</b><small data-user-text>#${number(item.number)}</small></div>
      ${price(item)}${checkout?'':`<button class="market-remove" data-act="marketRemove" aria-label="${t('marketRemove')}" data-arg="${item.id}">×</button>`}
    </div>`).join('');
    const modal = sheet(`<div class="market-sheet market-checkout${single?' market-checkout-single':''}"><h2>${t(checkout?'marketCheckout':'marketBasket')}</h2>
      ${items.length?`<div class="market-checkout-items">${body}</div>
        <div class="market-summary"><div class="market-money-line"><b>${t('marketTotal')}</b><span>${totals.map(item=>price(item)).join('')}</span></div>
        ${checkout&&balances?totals.map(item=>`<div class="market-money-line ${unavailable.has(item.currency)?'market-short':''}"><span>${t('marketBalance')}</span>
          ${price({currency:item.currency,price:balances[item.currency]||'0'})}</div>`).join(''):''}</div>
        ${checkout?`
          <p class="market-checkout-error" role="alert" ${unavailable.size?'':'hidden'}>${unavailable.size?t('marketInsufficient'):''}</p>
          <button class="btn market-pay" data-act="marketComplete" ${unavailable.size?'disabled':''}>${ico('cart')}${t('marketBuy')}</button>
          ${unavailable.size?`<button class="btn s market-pay" data-act="marketRecheck">${t('marketRecheck')}</button>`:''}
          <button class="btn s market-pay" data-act="marketClose" hidden>${t('marketClose')}</button>`:
          `<button class="btn market-pay" data-act="marketCheckout">${t('marketCheckout')}</button>`}`:`<p class="hint">${t('marketEmptyBasket')}</p>`}
      </div>`,opener);
    modal.classList.add('market-checkout-overlay');
    checkoutView = checkout?{items,modal}:null;
  }

  function syncBasket() {
    document.querySelectorAll('[data-market-basket-count]').forEach(el=>{
      if (el.textContent===String(basket.size)) return;
      el.textContent=basket.size;
      el.hidden=basket.size===0;
      if (!reduceMotion.matches) el.animate?.([{transform:'scale(.85)'},{transform:'scale(1.18)'},{transform:'scale(1)'}],220);
    });
    document.querySelectorAll('[data-act="marketAdd"]').forEach(button=>{
      const selected = basket.has(Number(button.dataset.arg));
      button.setAttribute('aria-pressed',String(selected));
      if (button.classList.contains('market-add-icon')) {
        button.innerHTML=ico(selected?'check':'cart');
        button.setAttribute('aria-label',t(selected?'marketInBasket':'marketAdd'));
      } else if(button.closest('.gates-gift-links')) button.innerHTML=ico('cart')+`<span>${t(selected?'marketInBasket':'marketAdd')}</span>`; else button.textContent=t(selected?'marketInBasket':'marketAdd');
    });
  }

  function observeMotion(root,previous){
    previous?.disconnect();
    if(!root||reduceMotion.matches||!window.IntersectionObserver)return;
    const visible=new Set();
    const update=()=>{
      const blocked=document.hidden||reduceMotion.matches||root.inert||
        (root.matches('main')&&document.querySelector('.sheet-wrap.open'));
      const active=new Set(blocked?[]:[...visible].slice(0,6));
      root.querySelectorAll('[data-market-motion]').forEach(target=>{
        let player=target.querySelector('lottie-player');
        if(!active.has(target)){player?.pause?.();return;}
        if(player){player.play?.();return;}
        if(target.dataset.motionFailed)return;
        player=document.createElement('lottie-player');
        for(const [key,value]of Object.entries({background:'transparent',renderer:'svg',loop:'','aria-hidden':'true'}))player.setAttribute(key,value);
        const failed=()=>{target.dataset.motionFailed='1';target.classList.remove('market-motion-ready');player.remove();};
        const ready=()=>{if(player.isConnected){target.classList.add('market-motion-ready');update();}};
        player.addEventListener('load',ready);
        player.addEventListener('ready',ready);
        player.addEventListener('error',failed);
        target.append(player);
        customElements.whenDefined('lottie-player').then(async()=>{
          const data=window.GatesMarketMotion
            ?await window.GatesMarketMotion(target.dataset.marketMotion)
            :`/static/market-motion/${target.dataset.marketMotion}.json`;
          if(player.isConnected){
            await player.updateComplete;
            await player.load(data);
            if(typeof data==='object')ready();
          }
        }).catch(failed);
      });
    };
    const watcher=new IntersectionObserver(entries=>{for(const e of entries)e.isIntersecting?visible.add(e.target):visible.delete(e.target);update();},{threshold:.25});
    root.querySelectorAll('[data-market-motion]').forEach(el=>watcher.observe(el));
    return {disconnect(){watcher.disconnect();root.querySelectorAll('.market-image lottie-player').forEach(player=>player.pause?.());}};
  }
  function afterRender() {
    observer=observeMotion(document.querySelector('main'),observer);
    pickerObserver=observeMotion(document.querySelector('.sheet-wrap.open'),pickerObserver);
  }
  document.addEventListener('visibilitychange',afterRender);
  document.addEventListener('ggsel-sheet-change',afterRender);
  reduceMotion.addEventListener('change',afterRender);

  function openGift(item,button){
    const id=item.offer_id||item.id;
    const traits=[['marketModel','model',item.model],['gatesSymbol','symbol',item.symbol],['marketBackdrop','backdrop',item.backdrop]];
    const property=(label,key,value)=>{
      const parts=String(value||'').match(/^(.*?)\s*\(([\d.,]+)%\)$/);
      return `<div><dt>${t(label)}</dt><dd><strong data-user-text>${esc(parts?parts[1]:value)}</strong>${parts?`<span class="gates-rarity">${esc(parts[2])}%</span>`:''}${item.trait_prices?.[key]?`<span class="gates-trait-price">${price({price:item.trait_prices[key],currency:item.currency},true)}</span>`:''}</dd></div>`;
    };
    const modal=sheet(`<div class="gates-gift-detail"><div class="gates-gift-hero">${image(item,true)}<div class="gates-gift-title"><h2 data-user-text>${esc(item.name)}</h2><span>#${item.number}</span></div></div><dl class="gates-gift-properties">${traits.filter(([,key,value])=>value).map(([label,key,value])=>property(label,key,value)).join('')}<div><dt>${t('gatesAveragePrice')}</dt><dd>${price({price:item.average_price||item.price,currency:item.currency},true)}</dd></div></dl><button class="btn gates-gift-buy" data-act="marketReviewOne" data-arg="${id}">${t('gatesBuyFor')} ${price(item,true)}</button></div>`,button);
    modal.classList.add('gates-gift-overlay');pickerObserver=observeMotion(modal,pickerObserver);
  }
  A.marketOpen=(id,button)=>busy(button,async()=>{let item=shown.get(Number(id));if(!item?.detailLoaded){item=await api('/market/listings/'+id);item.detailLoaded=true;shown.set(item.id,item);}openGift(item,button);});
  A.marketReviewOne=(id,button)=>{const item=shown.get(Number(id))||offers.get(Number(id));if(item)basketSheet(true,button,[item]);};
  A.marketSource = value => { source=value; favoritesOnly=false; filters.page=1; render(true); };
  A.marketCurrencyMenu = (_,button) => {
    filterModal(`<div class="market-sheet"><h2>${t('currency')}</h2><div class="market-currency-list">
    ${['',...currencies].map(currency=>`<button type="button" data-act="marketCurrency" data-arg="${currency}" aria-pressed="${filters.currency===currency}">
      <span class="market-currency-icon">${currency?currencyIcon(currency):ico('wallet')}</span>
      <span><b>${currency==='STARS'?'Stars':currency||t('marketAllCurrencies')}</b>${currency?`<small>${t(currencyLabels[currency])}</small>`:''}</span>
      ${filters.currency===currency?'<span class="market-picked" aria-hidden="true">✓</span>':''}
    </button>`).join('')}</div></div>`,button);
  };
  A.marketCurrency = currency => { Object.assign(filters,{currency,page:1}); closeSheet(); render(true); };
  A.marketPage = page => { filters.page=Number(page); render(); };
  A.marketReset = () => { Object.assign(filters,defaults); favoritesOnly=false; closeSheet(); render(true); };
  A.marketSaved = () => { favoritesOnly=!favoritesOnly; filters.page=1; render(true); };
  A.marketFavorite = (id,button) => {
    const key = Number(id);
    if (!favorites.delete(key)) favorites.add(key);
    if (favoritesOnly) { render(true); return; }
    button.setAttribute('aria-pressed',String(favorites.has(key)));
    button.innerHTML=(favorites.has(key)?'♥':'♡')+`<span>${t('marketFavorite')}</span>`;
  };
  A.marketAdd = id => {
    const key = Number(id);
    const item = shown.get(key);
    if (!item) return;
    if (!basket.delete(key)) basket.set(key,item);
    syncBasket();
  };
  A.marketBasket = (_,button) => basketSheet(false,button);
  A.marketRemove = id => { basket.delete(Number(id)); syncBasket(); basketSheet(false); };
  A.marketCheckout = () => { if (basket.size) basketSheet(true); };
  A.marketBuyOne = (id,button) => A.marketOpen(id,button);
  A.marketOffer = (id,button) => { const item=offers.get(Number(id)); if (item) basketSheet(true,button,[item]); };
  A.marketOfferDetail = (id,button) => { const item=offers.get(Number(id)); if (item) openGift(item,button); };
  A.marketMine = () => { closeSheet(); go('market-owned'); };

  function withdrawVideo(step) {
    const path = `/static/assets/animations/gift-withdraw-${step}`;
    return `<video src="${path}.mp4" poster="${path}.webp" width="128" height="128" muted playsinline
      ${reduceMotion.matches?'':'autoplay'} ${step==='instructions'?'loop':''} preload="metadata" aria-hidden="true"></video>`;
  }

  A.marketWithdraw = (_,button) => {
    const support = getMe()?.support;
    const opener = button.closest('.gates-account-panel') ? document.querySelector('[data-act="profileDrawer"]') : button;
    const modal = sheet(`<section class="market-withdraw"><h2 id="market-withdraw-title">${t('marketWithdrawTitle')}</h2>
      ${withdrawVideo('instructions')}
      <p>${t('marketWithdrawInstructions')}</p>
      ${support?`<button type="button" class="btn s" data-act="support">${t('marketWithdrawContact')} <span data-user-text>@${esc(support)}</span></button>`:''}
      <button type="button" class="btn" data-act="marketWithdrawDone">${t('marketClose')}</button>
    </section>`,opener);
    modal.querySelector('[role="dialog"]').setAttribute('aria-labelledby','market-withdraw-title');
  };

  A.marketWithdrawDone = (_,button) => {
    const panel = button.closest('.market-withdraw');
    panel.querySelector('video').pause();
    panel.innerHTML = `<h2 id="market-withdraw-title">${t('marketWithdrawTitle')}</h2>
      ${withdrawVideo('confirmation')}<p tabindex="-1">${t('marketWithdrawConfirmation')}</p>`;
    panel.querySelector('p').focus({preventScroll:true});
  };

  A.marketRecheck = (_,button) => busy(button,async()=>{
    const active=checkoutView;
    await refreshMe();
    if (active?.modal.classList.contains('open')) basketSheet(true,undefined,active.items);
  });
  A.marketComplete = (_,button) => busy(button,async()=>{
    const active=checkoutView;
    if (!active?.modal.contains(button)) return;
    const {items,modal}=active;
    const ids=items.map(item=>item.id), offer=items[0].offer_id;
    const errorBox=modal.querySelector('.market-checkout-error');
    errorBox.hidden=true;
    try {
      if (offer) await api(`/market/offers/${offer}/buy`,{method:'POST'});
      else await api('/market/buy',{method:'POST',body:{ids}});
    } catch (error) {
      errorBox.textContent=error.message;
      errorBox.hidden=false;
      if (error.status===409) {
        modal.afterClose=()=>render(true);
        button.hidden=true;
        modal.querySelector('[data-act="marketClose"]').hidden=false;
      }
      return;
    }
    checkoutView=null;
    if (offer) {
      offers.delete(offer);
      document.querySelector(`[data-act="marketOfferDetail"][data-arg="${offer}"]`)?.closest('.market-card')?.remove();
    } else {
      for (const id of ids) {
        basket.delete(id);
        shown.delete(id);
        document.querySelector(`[data-act="marketOpen"][data-arg="${id}"]`)?.closest('.market-card')?.remove();
      }
    }
    syncBasket();
    await refreshMe().catch(()=>{});
    if (!modal.classList.contains('open')) { render(true); return; }
    const success=sheet(`<div class="market-sheet market-checkout market-success"><h2>${t('marketCompleteTitle')}</h2>
      ${items.length===1?image({...items[0],animated:false},true):''}<p class="hint">${t('marketPurchasedHint')}</p>
      <button class="btn market-pay" data-act="marketMine">${t('marketMine')}</button>
      <button class="btn s market-pay" data-act="marketClose">${t('marketClose')}</button></div>`);
    success.classList.add('market-checkout-overlay');
    success.afterClose=()=>render(true);
  });
  A.marketClose = () => { closeSheet(); go('market'); };
  A.marketListGift = (id,button) => {
    const item=owned.get(Number(id));
    if (!item) return;
    const currency=item.offer_currency||item.currency;
    filterModal(`<div class="market-sheet"><h2>${t(item.offer_id?'marketEditOffer':'marketListGift')}</h2>
      <div class="market-list-gift"><b data-user-text>${esc(item.name)}</b> <span data-user-text>#${number(item.number)}</span></div>
      <form data-market-sell="${id}">
        <label>${t('currency')}<select name="currency">${currencies.map(code=>`<option value="${code}" ${currency===code?'selected':''}>${code==='STARS'?'Stars':code}</option>`).join('')}</select></label>
        <label>${t('marketSalePrice')}<input name="price" inputmode="decimal" required maxlength="22" value="${esc(item.offer_price||item.price)}"></label>
        <button type="submit" class="btn">${t(item.offer_id?'marketSaveOffer':'marketListGift')}</button>
      </form></div>`,button);
  };
  A.marketCancelOffer = (id,button) => busy(button,async()=>{
    await api(`/market/offers/${id}`,{method:'DELETE'});
    render(true);
  });
  function filterSheet() {
    pickerObserver?.disconnect();
    filterModal(`<div class="market-sheet"><h2>${t('marketFilters')}</h2>
    <form data-market-extra>
      ${control('marketVisual','backdrop','marketBackdrop',extraDraft.backdrop||t('marketAny'))}
      ${control('marketVisual','symbol','marketSymbol',extraDraft.symbol||t('marketAny'))}
      <div class="market-range"><label>${t('marketMin')}<input type="number" name="min" min="0" step="any" value="${esc(extraDraft.min)}"></label>
        <label>${t('marketMax')}<input type="number" name="max" min="0" step="any" value="${esc(extraDraft.max)}"></label></div>
      <button class="btn" type="submit">${t('marketApply')}</button>
    </form></div>`,extraOpener);
  }
  A.marketFilters = (_,button) => {
    extraOpener=button;
    const {backdrop,symbol,min,max}=filters;
    extraDraft={backdrop,symbol,min,max};
    filterSheet();
  };
  A.marketBackToFilters = () => filterSheet();

  function readFilterDraft(form) {
    for (const key of ['min','max']) extraDraft[key]=form.elements[key].value;
  }

  function visualArt(item) {
    if (item.symbol) return `<span class="market-symbol-art" aria-hidden="true"><img src="/static/market-symbol/${esc(item.symbol)}.webp" width="128" height="128" loading="lazy" alt=""></span>`;
    if (item.palette) {
      const [center,edge,pattern]=item.palette;
      return `<span class="market-swatch" aria-hidden="true" style="--swatch-center:${esc(center)};--swatch-edge:${esc(edge)};--swatch-pattern:${esc(pattern)}"></span>`;
    }
    return item.image_id ? image(item) : `<span class="market-symbol-art" aria-hidden="true">${ico('gem')}</span>`;
  }

  A.marketVisual = (kind,button) => busy(button,async()=>{
    const sourceSheet=button.closest('.sheet-wrap');
    const isExtra=Boolean(sourceSheet && extraTraits.has(kind));
    if (isExtra) readFilterDraft(button.closest('form'));
    const items=await previews(kind);
    if (!button.isConnected || (sourceSheet && !sourceSheet.classList.contains('open'))) return;
    picker={kind,items,isExtra};
    const current=isExtra?extraDraft[kind]:filters[kind];
    const modal=filterModal(`<div class="market-sheet market-visual-picker"><h2>${t(previewLabels[kind])}</h2>
      <input type="search" aria-label="${t('marketFindOption')}" placeholder="${t('marketFindOption')}" data-market-picker-query autocomplete="off">
      <button type="button" class="market-clear-choice" data-act="marketPickTrait" data-arg="-1" aria-pressed="${!current}">${t(clearLabels[kind]||'marketAny')}</button>
      <div class="market-option-grid">${items.map((item,index)=>`<button type="button" class="market-option" data-act="marketPickTrait" data-arg="${index}" aria-pressed="${item.value===current}" data-market-option="${esc(item.value)}">${visualArt(item)}
        <span data-user-text>${esc(item.value)}</span></button>`).join('')}</div>
      <p class="hint market-options-empty" hidden>${t('marketNoOptions')}</p>
      ${isExtra?`<button type="button" class="btn s" data-act="marketBackToFilters">${t('back')}</button>`:''}</div>`,isExtra?extraOpener:button);
    pickerObserver=observeMotion(modal,pickerObserver);
  });
  A.marketPickTrait = index => {
    const {kind,items,isExtra}=picker;
    const value=items[Number(index)]?.value || '';
    if (isExtra) { extraDraft[kind]=value; filterSheet(); return; }
    if (kind==='collection' && value!==filters.collection) Object.assign(filters,{model:'',backdrop:'',symbol:''});
    filters[kind]=value;
    filters.page=1;
    closeSheet();
    render(true);
  };
  document.addEventListener('input',event=>{
    if (!event.target.hasAttribute('data-market-picker-query')) return;
    const query=event.target.value.trim().toLocaleLowerCase(I.locale);
    const panel=event.target.closest('.market-visual-picker');
    let matches=0;
    for (const option of panel.querySelectorAll('[data-market-option]')) {
      option.hidden=!option.dataset.marketOption.toLocaleLowerCase(I.locale).includes(query);
      if (!option.hidden) matches++;
    }
    panel.querySelector('.market-options-empty').hidden=matches>0;
  });
  document.addEventListener('submit',event=>{
    if (event.target.matches('[data-market-search]')) {
      event.preventDefault();
      Object.assign(filters,{q:event.target.elements.q.value.trim(),page:1});
      render(true);
      return;
    }
    if (event.target.hasAttribute('data-market-sell')) {
      event.preventDefault();
      const form=event.target, item=owned.get(Number(form.dataset.marketSell));
      if (!item) return;
      busy(form.querySelector('[type="submit"]'),async()=>{
        await api(`/market/mine/${item.id}/offer`,{method:'PUT',body:{currency:form.elements.currency.value,price:form.elements.price.value,previous:item.offer_id}});
        closeSheet(); render(true);
      });
      return;
    }
    if (event.target.matches('[data-market-extra]')) {
      event.preventDefault();
      readFilterDraft(event.target);
      Object.assign(filters,extraDraft,{page:1});
      closeSheet();
      render(true);
      return;
    }
  });
  document.addEventListener('change',event=>{
    if (event.target.id !== 'market-sort') return;
    filters.sort=event.target.value;
    filters.page=1;
    render(true);
  });
  async function profileInventory(){
    const items=await api('/market/mine');owned.clear();for(const item of items)owned.set(item.id,item);
    const gifts = items.length?`<div class="market-grid profile-nfts">${items.map(item=>`<button class="profile-nft" data-act="marketOwnedDetail" data-arg="${item.id}">${image(item)}<strong data-user-text>${esc(item.name)}</strong><small>#${item.number}</small></button>`).join('')}</div>`:`<div class="profile-empty"><lottie-player class="loot-bag" src="/static/assets/animations/loot-bag.json" background="transparent" loop autoplay aria-hidden="true"></lottie-player><b>${t('gatesFirstGift')}</b><p>${t('gatesFirstGiftHint')}</p>${getMe()?.support?`<button class="profile-support" data-act="support" data-user-text>@${esc(getMe().support)}</button>`:''}</div>`;
    return `<header class="market-withdraw-header"><h2>${t('gatesPortfolio')}</h2>${withdrawButton()}</header>${gifts}`;
  }
  A.marketOwnedDetail=(id,button)=>{const item=owned.get(Number(id));if(!item)return;const modal=sheet(`<div class="gates-gift-hero">${image(item,true)}<div class="gates-gift-title"><h2 data-user-text>${esc(item.name)}</h2><span>#${item.number}</span></div></div><button class="btn" data-act="marketMine">${t('marketMine')}</button>`,button);modal.classList.add('gates-gift-overlay');pickerObserver=observeMotion(modal,pickerObserver);};
  return {afterRender,profileInventory};
};
