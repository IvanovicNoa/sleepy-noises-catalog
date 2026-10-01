// Landing page for links shared from the Sleepy Noises app:
//   …/listen/?show=<podcastId>&episode=<episode guid>
// It opens the app through its URL scheme and otherwise offers the App
// Store. Everything shown comes from the public catalog and RSS feed and is
// inserted as text, never as HTML.
(function () {
  'use strict';

  // Fill in once the app is live, e.g. 'https://apps.apple.com/app/id123'.
  var APP_STORE_URL = '';
  var MAX_ID = 256;

  function cleanId(value) {
    if (!value) return null;
    value = value.trim();
    if (!value || value.length > MAX_ID || /[\u0000-\u001f\u007f]/.test(value)) return null;
    return value;
  }

  var params = new URLSearchParams(window.location.search);
  var show = cleanId(params.get('show'));
  var episode = cleanId(params.get('episode'));

  function el(id) { return document.getElementById(id); }
  function setText(id, text) { if (text) el(id).textContent = text; }

  // sleepynoises://app/listen?show=…&episode=…
  var appQuery = new URLSearchParams();
  if (show) appQuery.set('show', show);
  if (episode) appQuery.set('episode', episode);
  var appUrl = 'sleepynoises://app/listen' + (show ? '?' + appQuery.toString() : '');
  el('open').setAttribute('href', appUrl);

  if (APP_STORE_URL) {
    el('store').setAttribute('href', APP_STORE_URL);
    el('store').hidden = false;
    el('soon').hidden = true;
  }

  function setArt(url) {
    if (!url || !/^https:\/\//.test(url)) return;
    var art = el('art');
    art.style.backgroundImage = 'url("' + url.replace(/["\\]/g, '') + '")';
    art.className = 'art has-image';
  }

  function formatDuration(text) {
    if (!text) return '';
    var parts = text.split(':').map(Number);
    if (parts.some(isNaN)) return '';
    var seconds = parts.reduce(function (acc, n) { return acc * 60 + n; }, 0);
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    if (h && m) return h + ' hr ' + m + ' min';
    if (h) return h + ' hr';
    return m + ' min';
  }

  function itunes(node, name) {
    var list = node.getElementsByTagNameNS('http://www.itunes.com/dtds/podcast-1.0.dtd', name);
    return list.length ? list[0] : null;
  }

  function plain(html) {
    var doc = new DOMParser().parseFromString(html || '', 'text/html');
    return (doc.body.textContent || '').trim().slice(0, 1200);
  }

  function showEpisode(feed) {
    var channel = feed.querySelector('channel');
    if (!channel) return;
    var title = channel.querySelector('title');
    if (title) setText('show', title.textContent.trim());
    var image = itunes(channel, 'image');
    var artwork = image ? image.getAttribute('href') : null;
    if (!episode) {
      if (title) setText('title', title.textContent.trim());
      setArt(artwork);
      return;
    }
    var items = channel.querySelectorAll('item');
    for (var i = 0; i < items.length; i++) {
      var guid = items[i].querySelector('guid');
      if (!guid || guid.textContent.trim() !== episode) continue;
      var t = items[i].querySelector('title');
      if (t) setText('title', t.textContent.trim());
      var itemImage = itunes(items[i], 'image');
      setArt((itemImage && itemImage.getAttribute('href')) || artwork);
      var date = items[i].querySelector('pubDate');
      var duration = itunes(items[i], 'duration');
      var meta = [];
      if (date) {
        var d = new Date(date.textContent);
        if (!isNaN(d)) meta.push(d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }));
      }
      var length = formatDuration(duration && duration.textContent.trim());
      if (length) meta.push(length);
      setText('meta', meta.join(' · '));
      var description = items[i].querySelector('description');
      if (description) setText('summary', plain(description.textContent));
      document.title = (t ? t.textContent.trim() + ' · ' : '') + 'Sleepy Noises';
      return;
    }
    setArt(artwork);
  }

  // Show and episode details: catalog → feed URL → RSS. Best effort only;
  // the buttons work without any of it.
  if (show) {
    fetch('../catalog.json', { credentials: 'omit' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (catalog) {
        var podcast = catalog && (catalog.podcasts || []).filter(function (p) { return p.id === show; })[0];
        if (!podcast) return null;
        if (podcast.title) setText('show', podcast.title);
        if (!/^https:\/\/feeds\.megaphone\.fm\//.test(podcast.feedUrl)) return null;
        return fetch(podcast.feedUrl, { credentials: 'omit' }).then(function (r) { return r.ok ? r.text() : null; });
      })
      .then(function (xml) {
        if (xml) showEpisode(new DOMParser().parseFromString(xml, 'application/xml'));
      })
      .catch(function () { /* details are optional */ });
  }

  // On phones, try the app straight away; if it is not installed nothing
  // happens and the page stays.
  if (show && /iPhone|iPad|iPod|Android/i.test(navigator.userAgent)) {
    window.setTimeout(function () { window.location.href = appUrl; }, 300);
  }
})();
