// Radar CAT62 Parser - Frontend Application
// Real-time track visualization and monitoring.
//
// Protocol note: the server pushes ONE message per track update, shaped like
//   { type:'track', ts, src:{sac,sic}, tn, pos:{lat,lon}, gs, hdg, id, addr, m3a }
// plus periodic { type:'stats', data:{...} }. This client keys tracks the same
// way the server does (derive_track_id) and culls them on a wall-clock timer.

const STALE_MS = 30000;          // drop a track after 30s with no update
const RECONNECT_MS = 3000;

class RadarClient {
    constructor() {
        this.ws = null;
        this.map = null;
        this.markers = new Map();
        this.tracks = new Map();     // key -> { ...msg, _lastSeen }
        this.config = { ws_port: 8765, tile_url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png' };
        this.init();
    }

    async init() {
        await this.loadConfig();
        this.setupMap();
        this.setupEventListeners();
        this.connect();
        // Wall-clock staleness sweep (independent of server push cadence).
        setInterval(() => this.cullStale(), 2000);
    }

    async loadConfig() {
        try {
            const r = await fetch('/api/config');
            if (r.ok) this.config = Object.assign(this.config, await r.json());
        } catch (e) {
            console.warn('Could not load /api/config; using defaults', e);
        }
    }

    // --- Track identity: mirror the server's derive_track_id ------------------
    static keyOf(t) {
        if (t.id) return String(t.id);
        if (t.addr) return String(t.addr);
        const s = t.src || {};
        return `${s.sac ?? '?'}.${s.sic ?? '?'}.${t.tn ?? '?'}`;
    }

    setupMap() {
        this.map = L.map('map').setView([51.5074, -0.1278], 6);
        // Graceful basemap: try the configured tiles; if they fail to load
        // (e.g. air-gapped ops room), fall back to a plain graticule so markers
        // stay usable.
        const layer = L.tileLayer(this.config.tile_url, {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 19,
        });
        let tileErrors = 0;
        layer.on('tileerror', () => {
            tileErrors += 1;
            if (tileErrors === 1) {
                console.warn('Map tiles unavailable; using offline graticule fallback');
                this.map.removeLayer(layer);
                this.enableGraticuleFallback();
                this.showToast('Map tiles offline — using coordinate grid', 'warning');
            }
        });
        layer.addTo(this.map);
    }

    // Draw a lon/lat grid directly on the map so operators still get spatial
    // reference with no tile server reachable.
    enableGraticuleFallback() {
        document.getElementById('map').classList.add('offline-basemap');
        const draw = () => {
            if (this._grid) this._grid.forEach(l => this.map.removeLayer(l));
            this._grid = [];
            const b = this.map.getBounds();
            const step = 1; // degrees
            const fl = (x) => Math.floor(x / step) * step;
            for (let lon = fl(b.getWest()); lon <= b.getEast(); lon += step) {
                this._grid.push(L.polyline([[b.getSouth(), lon], [b.getNorth(), lon]],
                    { color: '#2b4a63', weight: 0.5, interactive: false }).addTo(this.map));
            }
            for (let lat = fl(b.getSouth()); lat <= b.getNorth(); lat += step) {
                this._grid.push(L.polyline([[lat, b.getWest()], [lat, b.getEast()]],
                    { color: '#2b4a63', weight: 0.5, interactive: false }).addTo(this.map));
            }
        };
        draw();
        this.map.on('moveend zoomend', draw);
    }

    setupEventListeners() {
        document.getElementById('clear-btn').addEventListener('click', () => this.clearTracks());
        document.getElementById('refresh-btn').addEventListener('click', () => this.refreshStats());
    }

    connect() {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${proto}//${window.location.hostname}:${this.config.ws_port}`;

        try {
            this.ws = new WebSocket(wsUrl);
        } catch (e) {
            console.error('WebSocket construction failed:', e);
            setTimeout(() => this.connect(), RECONNECT_MS);
            return;
        }

        this.ws.onopen = () => {
            this.setConnectionStatus(true);
            this.showToast('Connected to radar server', 'success');
        };

        this.ws.onmessage = (event) => {
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (e) {
                console.error('Failed to parse message:', e);
                return;
            }
            this.handleMessage(data);
        };

        this.ws.onerror = () => {
            // onclose fires next and handles reconnect; avoid duplicate toasts.
            this.setConnectionStatus(false);
        };

        this.ws.onclose = () => {
            this.setConnectionStatus(false);
            setTimeout(() => this.connect(), RECONNECT_MS);
        };
    }

    handleMessage(data) {
        if (data.type === 'track') {
            this.updateTrack(data);
        } else if (data.type === 'stats') {
            this.updateStats(data.data || {});
        }
    }

    updateTrack(msg) {
        const lat = msg.pos && msg.pos.lat;
        const lon = msg.pos && msg.pos.lon;
        if (typeof lat !== 'number' || typeof lon !== 'number') return; // need a position to plot

        const key = RadarClient.keyOf(msg);
        msg._lastSeen = Date.now();
        this.tracks.set(key, msg);
        this.updateMarker(key, msg, lat, lon);
        this.updateTrackList();
        document.getElementById('track-count').textContent = this.tracks.size;
    }

    cullStale() {
        const now = Date.now();
        let changed = false;
        for (const [key, t] of this.tracks) {
            if (now - t._lastSeen > STALE_MS) {
                this.tracks.delete(key);
                const m = this.markers.get(key);
                if (m) { this.map.removeLayer(m); this.markers.delete(key); }
                changed = true;
            }
        }
        if (changed) {
            this.updateTrackList();
            document.getElementById('track-count').textContent = this.tracks.size;
        }
    }

    updateMarker(key, track, lat, lon) {
        const heading = track.hdg || 0;
        let marker = this.markers.get(key);
        if (!marker) {
            marker = L.marker([lat, lon], { icon: this.createTrackIcon(heading) })
                .bindPopup(this.createPopup(key, track))
                .addTo(this.map);
            this.markers.set(key, marker);
        } else {
            marker.setLatLng([lat, lon]);
            marker.setIcon(this.createTrackIcon(heading));
            marker.setPopupContent(this.createPopup(key, track));
        }
    }

    createTrackIcon(heading) {
        const rotated = (heading + 90) % 360;
        const html = `<div style="transform: rotate(${rotated}deg); font-size: 24px;">✈️</div>`;
        return L.divIcon({ html, iconSize: [32, 32], className: 'track-marker' });
    }

    createPopup(key, track) {
        const speed = typeof track.gs === 'number' ? track.gs.toFixed(1) : '--';
        const heading = typeof track.hdg === 'number' ? track.hdg.toFixed(0) : '--';
        const updated = track.ts ? new Date(track.ts * 1000).toLocaleTimeString() : '--';
        return `
            <div class="track-popup">
                <strong>${key}</strong><br>
                Speed: ${speed} kt<br>
                Heading: ${heading}°<br>
                Updated: ${updated}
            </div>
        `;
    }

    updateTrackList() {
        const list = document.getElementById('track-list');
        const tracks = Array.from(this.tracks.entries()).slice(0, 12);
        if (tracks.length === 0) {
            list.innerHTML = '<p class="empty-message">No tracks</p>';
            return;
        }
        list.innerHTML = tracks.map(([key, t]) => `
            <div class="track-item">
                <div class="track-callsign">${key}</div>
                <div class="track-info">
                    <span>${typeof t.gs === 'number' ? t.gs.toFixed(0) : '--'} kt</span>
                    <span>${typeof t.hdg === 'number' ? t.hdg.toFixed(0) : '--'}°</span>
                </div>
            </div>
        `).join('');
    }

    updateStats(s) {
        if (typeof s.messages_received === 'number') {
            document.getElementById('message-count').textContent = s.messages_received;
        }
        if (typeof s.avg_speed === 'number') {
            document.getElementById('avg-speed').textContent = `${s.avg_speed.toFixed(0)} kt`;
        }
        if (typeof s.max_speed === 'number') {
            document.getElementById('max-speed').textContent = `${s.max_speed.toFixed(0)} kt`;
        }
        if (typeof s.messages_received === 'number' && s.messages_received > 0) {
            const rate = ((s.messages_parsed / s.messages_received) * 100).toFixed(1);
            document.getElementById('success-rate').textContent = `${rate}%`;
        }
    }

    setConnectionStatus(connected) {
        const dot = document.getElementById('connection-status');
        const text = document.getElementById('connection-text');
        const value = document.getElementById('status-value');
        dot.className = connected ? 'status-dot connected' : 'status-dot disconnected';
        text.textContent = connected ? 'Connected' : 'Disconnected';
        value.textContent = connected ? 'Connected' : 'Disconnected';
    }

    clearTracks() {
        this.tracks.clear();
        this.markers.forEach(marker => this.map.removeLayer(marker));
        this.markers.clear();
        document.getElementById('track-count').textContent = '0';
        this.updateTrackList();
        this.showToast('Tracks cleared', 'info');
    }

    refreshStats() {
        fetch('/api/stats')
            .then(r => r.json())
            .then(data => this.updateStats(data))
            .catch(e => console.error('Failed to refresh stats:', e));
    }

    showToast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.radarClient = new RadarClient();
});
