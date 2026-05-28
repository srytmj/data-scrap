# deploy.sh
#!/bin/bash
# Deployment script for AWS EC2

set -e

echo "🚀 Deploying Medan Price Scraper to AWS..."

# Update system
sudo apt-get update
sudo apt-get upgrade -y

# Install Python and dependencies
sudo apt-get install -y python3 python3-pip python3-venv

# Create application directory
sudo mkdir -p /opt/medan-scraper
sudo chown ubuntu:ubuntu /opt/medan-scraper
cd /opt/medan-scraper

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install playwright playwright-stealth aiohttp
playwright install chromium
playwright install-deps

# Copy application files
# (You'll need to upload the files or use git)
# For now, assuming files are in current directory
cp app.py scraper.py /opt/medan-scraper/

# Create data directory
mkdir -p data

# Set up cron job for daily execution
(crontab -l 2>/dev/null; echo "0 8 * * * cd /opt/medan-scraper && source venv/bin/activate && python app.py >> /var/log/medan-scraper.log 2>&1") | crontab -

# Create log rotation
sudo tee /etc/logrotate.d/medan-scraper << EOF
/var/log/medan-scraper.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 ubuntu ubuntu
}
EOF

echo "✅ Deployment complete!"
echo "Logs: /var/log/medan-scraper.log"
echo "Data: /opt/medan-scraper/data/"