// Configuração
const API_BASE = '';
const UPDATE_INTERVAL = 2000; // 2 segundos
const LOG_LINES_TO_KEEP = 200;

let logContainer = null;

// Inicialização
document.addEventListener('DOMContentLoaded', () => {
    logContainer = document.getElementById('liveLog');
    updateStats();
    updateLog();
    updateClock();
    
    // Atualizar periodicamente
    setInterval(updateStats, UPDATE_INTERVAL);
    setInterval(updateLog, UPDATE_INTERVAL);
    setInterval(updateClock, 1000); // Atualizar relógio a cada segundo
    
    // Indicador de status
    updateStatus(true);
});

function updateClock() {
    const clockEl = document.getElementById('clock');
    if (clockEl) {
        const now = new Date();
        const hours = String(now.getHours()).padStart(2, '0');
        const minutes = String(now.getMinutes()).padStart(2, '0');
        const seconds = String(now.getSeconds()).padStart(2, '0');
        clockEl.textContent = `${hours}:${minutes}:${seconds}`;
    }
}

async function updateStats() {
    try {
        const response = await fetch(`${API_BASE}/api/stats`);
        const data = await response.json();
        
        // Atualizar balance
        document.getElementById('balance').textContent = `$${data.balance.toFixed(2)}`;
        document.getElementById('initialBalance').textContent = data.initial_balance.toFixed(2);
        
        // ROI
        const roiEl = document.getElementById('roi');
        const roi = data.roi || 0;
        roiEl.textContent = `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%`;
        roiEl.className = `roi ${roi >= 0 ? 'positive' : 'negative'}`;
        
        // Trades
        document.getElementById('totalTrades').textContent = data.total_trades || 0;
        document.getElementById('openTrades').textContent = data.open_trades || 0;
        document.getElementById('closedTrades').textContent = data.closed_trades || 0;
        
        // Performance
        document.getElementById('wins').textContent = data.wins || 0;
        document.getElementById('losses').textContent = data.losses || 0;
        const winRate = data.win_rate || 0;
        document.getElementById('winRate').textContent = `${winRate.toFixed(1)}%`;
        
        // P&L
        const pnl = data.total_pnl || 0;
        document.getElementById('totalPnl').textContent = `$${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}`;
        document.getElementById('totalPnl').style.color = pnl >= 0 ? '#4caf50' : '#f44336';
        document.getElementById('dailyPnl').textContent = (data.daily_pnl || 0).toFixed(2);
        
        // Trades recentes
        updateRecentTrades(data.recent_trades || []);
        
        updateStatus(true);
    } catch (error) {
        console.error('Erro ao atualizar stats:', error);
        updateStatus(false);
    }
}

async function updateLog() {
    try {
        const response = await fetch(`${API_BASE}/api/log`);
        const data = await response.json();
        
        if (!logContainer) return;
        
        // Limitar número de linhas
        const lines = data.lines || [];
        const recentLines = lines.slice(-LOG_LINES_TO_KEEP);
        
        // Atualizar log
        logContainer.innerHTML = recentLines.map(line => {
            if (!line) return '';
            
            const timestamp = line.timestamp || '';
            const message = line.message || '';
            const type = line.type || 'info';
            
            return `<div class="log-line ${type}">${timestamp} ${message}</div>`;
        }).join('');
        
        // Auto-scroll para o final
        logContainer.scrollTop = logContainer.scrollHeight;
        
    } catch (error) {
        console.error('Erro ao atualizar log:', error);
    }
}

function updateRecentTrades(trades) {
    const tbody = document.getElementById('recentTradesBody');
    
    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty">Nenhum trade ainda</td></tr>';
        return;
    }
    
    // Mostrar últimos 50 trades
    tbody.innerHTML = trades.slice(0, 50).map(trade => {
        const timestamp = trade.timestamp || '';
        const market = trade.market || '-';
        let type = '-';
        let price = '-';
        let result = '-';
        let pnl = '-';
        
        if (trade.type === 'enter') {
            type = `<span class="badge enter">ENTER ${trade.side || ''}</span>`;
            price = trade.entry_price ? `$${trade.entry_price.toFixed(2)}` : '-';
            result = '<span class="badge">Aberto</span>';
            pnl = '-';
        } else if (trade.type === 'closed') {
            type = `<span class="badge closed">CLOSED</span>`;
            price = '-';
            const won = trade.result === trade.side;
            result = won 
                ? `<span class="badge won">✅ ${trade.result}</span>`
                : `<span class="badge lost">❌ ${trade.result}</span>`;
            pnl = trade.pnl !== undefined 
                ? `<span style="color: ${trade.pnl >= 0 ? '#4caf50' : '#f44336'}">$${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)}</span>`
                : '-';
        } else if (trade.type === 'blocked') {
            type = `<span class="badge blocked">BLOCKED</span>`;
            result = trade.reason || '-';
        }
        
        return `
            <tr>
                <td>${timestamp}</td>
                <td>${market}</td>
                <td>${type}</td>
                <td>${price}</td>
                <td>${result}</td>
                <td>${pnl}</td>
            </tr>
        `;
    }).join('');
}

function updateStatus(active) {
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    
    if (active) {
        dot.classList.add('active');
        text.textContent = 'Online';
    } else {
        dot.classList.remove('active');
        text.textContent = 'Offline';
    }
}

function clearLog() {
    if (logContainer) {
        logContainer.innerHTML = '';
    }
}

