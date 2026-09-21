// Git commit graph visualization
(function() {
    const LANE_WIDTH = 30;
    const NODE_RADIUS = 5;
    const ROW_HEIGHT = 40;
    const COLORS = [
        '#3B6DFF', '#6EE7B7', '#E9A568', '#38BDF8',
        '#F472B6', '#A78BFA', '#FCD34D', '#FB923C'
    ];

    function renderGraph(containerId, graphData) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');

        const commits = graphData.commits;
        const maxLane = Math.max(...commits.map(c => c.lane));
        const width = (maxLane + 2) * LANE_WIDTH + 400;
        const height = commits.length * ROW_HEIGHT + 40;

        canvas.width = width;
        canvas.height = height;
        canvas.style.width = width + 'px';
        canvas.style.height = height + 'px';

        container.innerHTML = '';
        container.appendChild(canvas);

        // Draw edges first
        commits.forEach((commit, idx) => {
            const y = idx * ROW_HEIGHT + 20;
            const x = commit.lane * LANE_WIDTH + 20;

            commit.parent_lanes.forEach(parentLane => {
                const parentIdx = commits.findIndex(c => c.sha === commit.parents[commit.parent_lanes.indexOf(parentLane)]);
                if (parentIdx === -1) return;

                const parentY = parentIdx * ROW_HEIGHT + 20;
                const parentX = parentLane * LANE_WIDTH + 20;

                ctx.strokeStyle = COLORS[commit.lane % COLORS.length];
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(x, y);

                if (commit.lane === parentLane) {
                    // Straight line
                    ctx.lineTo(parentX, parentY);
                } else {
                    // Curved line
                    const midY = (y + parentY) / 2;
                    ctx.bezierCurveTo(x, midY, parentX, midY, parentX, parentY);
                }
                ctx.stroke();
            });
        });

        // Draw nodes
        commits.forEach((commit, idx) => {
            const y = idx * ROW_HEIGHT + 20;
            const x = commit.lane * LANE_WIDTH + 20;

            ctx.fillStyle = COLORS[commit.lane % COLORS.length];
            ctx.beginPath();
            ctx.arc(x, y, NODE_RADIUS, 0, Math.PI * 2);
            ctx.fill();

            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 2;
            ctx.stroke();

            // Draw commit message
            ctx.fillStyle = '#202020';
            ctx.font = '13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
            const message = commit.message.split('\n')[0];
            const truncated = message.length > 50 ? message.substring(0, 47) + '...' : message;
            ctx.fillText(truncated, x + 15, y + 4);

            // Draw sha
            ctx.fillStyle = '#737373';
            ctx.font = '11px "SFMono-Regular", Consolas, monospace';
            ctx.fillText(commit.sha.substring(0, 7), x + 15 + ctx.measureText(truncated).width + 10, y + 4);
        });

        // Add click handler
        canvas.addEventListener('click', (e) => {
            const rect = canvas.getBoundingClientRect();
            const clickX = (e.clientX - rect.left) * canvas.width / rect.width;
            const clickY = (e.clientY - rect.top) * canvas.height / rect.height;

            commits.forEach((commit, idx) => {
                const y = idx * ROW_HEIGHT + 20;
                const x = commit.lane * LANE_WIDTH + 20;
                const dist = Math.sqrt(Math.pow(clickX - x, 2) + Math.pow(clickY - y, 2));

                if (dist <= NODE_RADIUS + 3) {
                    showCommitDetail(commit);
                }
            });
        });

        canvas.style.cursor = 'pointer';
    }

    function showCommitDetail(commit) {
        const detail = document.getElementById('commit-detail');
        if (!detail) return;

        const date = new Date(commit.timestamp * 1000).toLocaleString('zh-CN');

        detail.innerHTML = `
            <h3>提交详情</h3>
            <p><strong>SHA:</strong> <code>${commit.sha}</code></p>
            <p><strong>作者:</strong> ${escapeHtml(commit.author)}</p>
            <p><strong>时间:</strong> ${date}</p>
            <p><strong>消息:</strong></p>
            <pre style="white-space: pre-wrap; margin: 8px 0;">${escapeHtml(commit.message)}</pre>
            ${commit.parents.length > 0 ? `<p><strong>父提交:</strong> ${commit.parents.map(p => `<code>${p.substring(0, 7)}</code>`).join(', ')}</p>` : ''}
        `;
        detail.hidden = false;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    window.renderGitGraph = renderGraph;
})();
