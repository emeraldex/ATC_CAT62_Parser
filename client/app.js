// Radar CAT62 Parser - Frontend Application
// Real-time track visualization and monitoring

class RadarClient {
    constructor() {
        this.ws = null;
        this.map = null;
        this.markers = new Map();
        this.tracks = new Map();
        this.stats = { messages: 0, parsed: 0, failed: 0, speeds: [], headings: [] };
        
        this.init();
    }

    init() {
        this.setupMap();
        this.setupEventListeners();
        this.connect();
    }

    setupMap() {
        // Initialize Leaflet map
        this.map = L.map('map').setView([51.5074, -0.1278], 6);
        
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 19
        }).addTo(this.map);
    }

    setupEventListeners() {
        document.getElementById('clear-btn').addEventListener('click', () => this.clearTracks());
        document.getElementById('refresh-btn').addEventListener('click', () => this.refreshStats());
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            this.setConnectionStatus(true);
            this.showToast('Connected to radar server', 'success');
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (e) {
                console.error('Failed to parse message:', e);
            }
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.showToast('Connection error', 'error');
        };

        this.ws.onclose = () => {
            this.setConnectionStatus(false);
            this.showToast('Disconnected from server', 'warning');
            // Attempt to reconnect after 3 seconds
            setTimeout(() => this.connect(), 3000);
        };
    }

    handleMessage(data) {
        if (data.type === 'tracks') {
            this.updateTracks(data.data);
        } else if (data.type === 'stats') {
            this.updateStats(data.data);
        }
    }

    updateTracks(tracksData) {
        if (!Array.isArray(tracksData)) return;

        const now = Date.now();

        tracksData.forEach(track => {
            const trackId = track.track_id;
            
            if (track.pos_lat !== null && track.pos_lon !== null) {
                this.tracks.set(trackId, track);
                this.updateMarker(trackId, track);
            }
        });

        // Remove stale markers (older than 60 seconds)
        this.markers.forEach((marker, trackId) => {
            if (!this.tracks.has(trackId)) {
                this.map.removeLayer(marker);
                this.markers.delete(trackId);
            }
        });

        this.updateTrackList();
        document.getElementById('track-count').textContent = this.tracks.size;
    }

    updateMarker(trackId, track) {
        const lat = track.pos_lat;
        const lon = track.pos_lon;
        const speed = track.ground_speed || 0;
        const heading = track.heading || 0;

        let marker = this.markers.get(trackId);

        if (!marker) {
            // Create new marker
            const icon = this.createTrackIcon(heading);
            marker = L.marker([lat, lon], { icon: icon })
                .bindPopup(this.createPopup(track))
                .addTo(this.map);
            this.markers.set(trackId, marker);
        } else {
            // Update existing marker
            marker.setLatLng([lat, lon]);
            marker.setIcon(this.createTrackIcon(heading));
            marker.setPopupContent(this.createPopup(track));
        }
    }

    createTrackIcon(heading) {
        const rotatedHeading = (heading + 90) % 360;
        const html = `<div style="transform: rotate(${rotatedHeading}deg); font-size: 24px;">✈️</div>`;
        
        return L.divIcon({
            html: html,
            iconSize: [32, 32],
            className: 'track-marker'
        });
    }

    createPopup(track) {
        const speed = track.ground_speed ? track.ground_speed.toFixed(1) : '--';
        const heading = track.heading ? track.heading.toFixed(0) : '--';
        const altitude = track.altitude ? track.altitude.toFixed(0) : '--';

        return `
            <div class="track-popup">
                <strong>${track.track_id}</strong><br>
                Speed: ${speed} kt<br>
                Heading: ${heading}°<br>
                Altitude: ${altitude} ft<br>
                Updated: ${new Date(track.timestamp * 1000).toLocaleTimeString()}
            </div>
        `;
    }

    updateTrackList() {
        const list = document.getElementById('track-list');
        const tracks = Array.from(this.tracks.values()).slice(0, 10);

        if (tracks.length === 0) {
            list.innerHTML = '<p class="empty-message">No tracks</p>';
            return;
        }

        const html = tracks.map(track => `
            <div class="track-item">
                <div class="track-callsign">${track.track_id}</div>
                <div class="track-info">
                    <span>${track.ground_speed ? track.ground_speed.toFixed(0) : '--'} kt</span>
                    <span>${track.heading ? track.heading.toFixed(0) : '--'}°</span>
                </div>
            </div>
        `).join('');

        list.innerHTML = html;
    }

    updateStats(statsData) {
        if (statsData.messages_received) {
            document.getElementById('message-count').textContent = statsData.messages_received;
        }

        // Calculate average and max speed
        if (statsData.speeds && statsData.speeds.length > 0) {
            const avgSpeed = (statsData.speeds.reduce((a, b) => a + b, 0) / statsData.speeds.length).toFixed(0);
            const maxSpeed = Math.max(...statsData.speeds).toFixed(0);
            document.getElementById('avg-speed').textContent = `${avgSpeed} kt`;
            document.getElementById('max-speed').textContent = `${maxSpeed} kt`;
        }

        // Calculate success rate
        if (statsData.messages_received > 0) {
            const successRate = ((statsData.messages_parsed / statsData.messages_received) * 100).toFixed(1);
            document.getElementById('success-rate').textContent = `${successRate}%`;
        }
    }

    setConnectionStatus(connected) {
        const statusDot = document.getElementById('connection-status');
        const statusText = document.getElementById('connection-text');
        const statusValue = document.getElementById('status-value');

        if (connected) {
            statusDot.className = 'status-dot connected';
            statusText.textContent = 'Connected';
            statusValue.textContent = 'Connected';
        } else {
            statusDot.className = 'status-dot disconnected';
            statusText.textContent = 'Disconnected';
            statusValue.textContent = 'Disconnected';
        }
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

// Initialize the app when the page loads
document.addEventListener('DOMContentLoaded', () => {
    new RadarClient();
});
