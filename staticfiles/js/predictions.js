// static/js/predictions.js

class PredictionsManager {
    constructor() {
        this.currentFilters = {};
        this.page = 1;
        this.loading = false;
        this.initializeEventListeners();
    }
    
    initializeEventListeners() {
        // Filter controls
        const applyButton = document.getElementById('applyFilters');
        const clearButton = document.getElementById('clearFilters');
        const refreshButton = document.getElementById('refreshPredictions');
        
        if (applyButton) applyButton.addEventListener('click', () => this.applyFilters());
        if (clearButton) clearButton.addEventListener('click', () => this.clearFilters());
        if (refreshButton) refreshButton.addEventListener('click', () => this.refreshPredictions());
        
        // Save prediction buttons
        document.addEventListener('click', (e) => {
            if (e.target.closest('.save-prediction')) {
                const predictionId = e.target.closest('.save-prediction').dataset.predictionId;
                this.savePrediction(predictionId);
            }
        });
    }
    
    updateFilters() {
        this.currentFilters = {
            leagues: $('#leagueFilter').val() || [],
            confidence: $('#confidenceFilter').val() || 'all',
            risk: $('#riskFilter').val() || 'all',
            date_range: $('.date-range').val() || ''
        };
    }
    
    applyFilters() {
        this.updateFilters();
        this.loadPredictions();
    }
    
    async loadPredictions() {
        if (this.loading) return;
        
        this.loading = true;
        showLoadingSpinner();
        
        try {
            const response = await $.ajax({
                url: '/api/predictions/filter/',
                method: 'POST',
                data: JSON.stringify(this.currentFilters),
                contentType: 'application/json'
            });
            
            this.renderPredictions(response);
            showToast('Predictions loaded successfully', 'success');
        } catch (error) {
            console.error('Error loading predictions:', error);
            showToast('Error loading predictions. Please try again.', 'error');
        } finally {
            this.loading = false;
            hideLoadingSpinner();
        }
    }
    
    renderPredictions(data) {
        const container = document.getElementById('predictionsContainer');
        
        if (!data.predictions || data.predictions.length === 0) {
            container.innerHTML = `
                <div class="col-12">
                    <div class="empty-state elegant-card text-center py-5">
                        <i class="fas fa-search fa-3x text-muted mb-3"></i>
                        <h4 class="mb-2">No predictions found</h4>
                        <p class="text-muted mb-4">Try adjusting your filters or check back later.</p>
                    </div>
                </div>
            `;
            return;
        }
        
        let html = '';
        data.predictions.forEach((prediction, index) => {
            html += this.renderPredictionCard(prediction, index);
        });
        
        container.innerHTML = html;
        
        // Add animation delay to each card
        document.querySelectorAll('.prediction-card').forEach((card, index) => {
            card.style.animationDelay = (index * 0.1) + 's';
            card.classList.add('fade-in-up');
        });
    }
    
    renderPredictionCard(prediction, index) {
        const confidenceClass = prediction.confidence_score >= 75 ? 'success' : 
                               prediction.confidence_score >= 55 ? 'warning' : 'danger';
        
        return `
            <div class="col-xl-4 col-lg-6 mb-4">
                <div class="prediction-card elegant-card h-100 ${prediction.predicted_outcome.replace('_', '-')}">
                    <div class="match-header d-flex justify-content-between align-items-center mb-3">
                        <span class="league-badge ${prediction.league_class}">${prediction.league}</span>
                        <small class="text-muted">${prediction.match_date}</small>
                    </div>
                    
                    <div class="match-teams d-flex justify-content-between align-items-center mb-3">
                        <div class="team text-center flex-fill">
                            <div class="fw-bold">${prediction.home_team}</div>
                        </div>
                        <div class="vs-text text-muted mx-2">vs</div>
                        <div class="team text-center flex-fill">
                            <div class="fw-bold">${prediction.away_team}</div>
                        </div>
                    </div>
                    
                    <div class="prediction-result text-center mb-3">
                        <span class="badge bg-${confidenceClass} px-3 py-2">
                            ${prediction.predicted_outcome.replace(/_/g, ' ').toUpperCase()}
                        </span>
                        <div class="mt-2">
                            <small class="text-muted">${prediction.confidence_score}% confidence</small>
                        </div>
                    </div>
                    
                    <div class="confidence-meter mb-3">
                        <div class="confidence-fill" style="width: ${prediction.confidence_score}%"></div>
                    </div>
                    
                    <div class="d-grid gap-2">
                        <a href="/predictions/${prediction.id}/" class="btn btn-outline-primary btn-sm">
                            View Details
                        </a>
                    </div>
                </div>
            </div>
        `;
    }
    
    clearFilters() {
        document.getElementById('leagueFilter').value = '';
        document.getElementById('confidenceFilter').value = 'all';
        document.getElementById('riskFilter').value = 'all';
        document.querySelector('.date-range').value = '';
        
        this.currentFilters = {};
        this.loadPredictions();
        showToast('Filters cleared', 'info');
    }
    
    async refreshPredictions() {
        const button = document.getElementById('refreshPredictions');
        if (button) button.classList.add('fa-spin');
        
        await this.loadPredictions();
        
        if (button) button.classList.remove('fa-spin');
        showToast('Predictions refreshed', 'success');
    }
    
    async savePrediction(predictionId) {
        try {
            await $.ajax({
                url: `/api/predictions/${predictionId}/save/`,
                method: 'POST'
            });
            showToast('Prediction saved to favorites', 'success');
        } catch (error) {
            showToast('Error saving prediction', 'error');
        }
    }
}

// Initialize when page loads
$(document).ready(function() {
    window.predictionsManager = new PredictionsManager();
});