# Medan Commodity Price Scraper 

A robust, production-ready web scraper for collecting commodity price data from the official Medan City government website (https://simpang.medan.go.id/?menu=harga). The scraper automatically handles Cloudflare protection, pagination, and includes retry logic for reliable data collection in cloud environments.

## Overview

This tool scrapes daily commodity prices (lowest, highest, and average prices) across all kecamatan (districts) in Medan. It's designed for automated deployment on AWS with built-in error handling and monitoring capabilities.

### Features

- ✅ **Automatic Cloudflare Bypass** - Uses playwright-stealth to avoid detection
- ✅ **Full Pagination Support** - Scrapes all pages of data automatically
- ✅ **Smart Retry Logic** - Automatically retries on Cloudflare blocks (up to 3 times)
- ✅ **Production Ready** - Logging, state management, and backup creation
- ✅ **AWS Optimized** - Designed for EC2, ECS, or Lambda deployment
- ✅ **Monitoring Ready** - Slack webhook integration for alerts
- ✅ **Data Validation** - Verifies output integrity before saving

## Architecture

```
app.py (Orchestrator)
    ├── Manages retry logic
    ├── Handles Cloudflare detection
    ├── Creates backups
    └── Sends alerts

scraper.py (Core Scraper)
    ├── Browser automation
    ├── Data extraction
    ├── Pagination handling
    └── CSV generation

Output: harga_medan.csv
Logs: scraper.log, scraper_state.json
```

## Quick Start

### Local Development

```bash
# Clone repository
git clone <your-repo>
cd medan-scraper

# Install dependencies
pip install playwright playwright-stealth aiohttp
playwright install chromium

# Run once
python app.py --once

# Run with retries
python app.py --max-retries 5
```

### Docker Deployment

```bash
# Build image
docker build -t medan-scraper .

# Run container
docker run -v $(pwd)/data:/app/data medan-scraper

# Using docker-compose
docker-compose up -d
```

## AWS Deployment Guide

### Option 1: EC2 (Recommended for daily scraping)

#### Step 1: Launch EC2 Instance
```bash
# Launch t3.micro or larger (2GB RAM minimum)
# AMI: Ubuntu 22.04 LTS
# Security group: Allow SSH (22) only
```

#### Step 2: Deploy Application
```bash
# SSH into instance
ssh -i your-key.pem ubuntu@your-ec2-ip

# Clone and deploy
git clone <your-repo>
cd medan-scraper
chmod +x deploy.sh
./deploy.sh
```

#### Step 3: Set Up Daily Cron
The deploy script automatically adds a cron job. Verify:
```bash
crontab -l
# Should show: 0 8 * * * cd /opt/medan-scraper && python app.py >> /var/log/medan-scraper.log
```

#### Step 4: Monitor
```bash
# Check logs
tail -f /var/log/medan-scraper.log

# Check data
ls -la /opt/medan-scraper/data/
```

### Option 2: ECS/Fargate (Serverless)

```bash
# Build and push to ECR
aws ecr create-repository --repository-name medan-scraper
docker build -t medan-scraper .
docker tag medan-scraper:latest <account-id>.dkr.ecr.<region>.amazonaws.com/medan-scraper:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/medan-scraper:latest

# Create ECS Task Definition (example below)
```

**ECS Task Definition (task-def.json):**
```json
{
  "family": "medan-scraper",
  "taskRoleArn": "arn:aws:iam::<account-id>:role/ecsTaskRole",
  "executionRoleArn": "arn:aws:iam::<account-id>:role/ecsExecutionRole",
  "networkMode": "awsvpc",
  "containerDefinitions": [{
    "name": "scraper",
    "image": "<account-id>.dkr.ecr.<region>.amazonaws.com/medan-scraper:latest",
    "memory": 1024,
    "cpu": 512,
    "command": ["python", "app.py", "--max-retries", "3"],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/medan-scraper",
        "awslogs-region": "<region>",
        "awslogs-stream-prefix": "medan-scraper"
      }
    }
  }],
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024"
}
```

Schedule with EventBridge:
- Rule: `cron(0 8 * * ? *)` (8 AM daily)
- Target: ECS Task

### Option 3: Lambda (Lightweight alternative)

**Limitations:** Lambda has 15-minute timeout, suitable if scraping <10 kecamatan

```bash
# Package for Lambda
pip install playwright playwright-stealth -t lambda-package
cp app.py scraper.py lambda-package/
cd lambda-package
zip -r ../lambda-function.zip .
```

**Lambda Configuration:**
- Runtime: Python 3.11
- Memory: 1024 MB
- Timeout: 10 minutes
- Layers: Add AWS provided layer for Chromium

## File Structure

```
medan-scraper/
├── app.py              # Orchestrator with retry logic
├── scraper.py          # Core scraping logic
├── deploy.sh           # EC2 deployment script
├── Dockerfile          # Docker build instructions
├── docker-compose.yml  # Docker orchestration
├── requirements.txt    # Python dependencies
├── README.md          # This file
└── data/              # Output directory (created at runtime)
    ├── harga_medan.csv           # Latest data
    └── harga_medan_*.csv         # Historical backups
```

## Configuration

### Environment Variables
```bash
# Optional: Slack notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz

# Optional: S3 backup
AWS_S3_BUCKET=your-bucket-name
AWS_S3_PREFIX=prices/medan/

# Scraper settings
MAX_RETRIES=3
RETRY_DELAY=30  # seconds
```

### Command Line Arguments
```bash
python app.py --once              # Run once without retry
python app.py --max-retries 5     # Maximum retry attempts
python app.py --no-cleanup        # Skip debug file cleanup
```

## Output Format

### CSV Structure
```csv
id,kecamatan,komoditas,harga_terendah,harga_tertinggi,harga_rata_rata
1,Medan Baru,Beras Premium,"14,000","15,000","14,500"
2,Medan Baru,Beras Medium,"13,000","13,500","13,250"
...
```

### Log Files
- `scraper.log` - Detailed execution logs
- `scraper_state.json` - Last run status and error tracking
- `debug.png` - Screenshot when Cloudflare blocks (auto-cleaned)

## Error Handling

### Cloudflare Detection
The system automatically:
1. Detects Cloudflare challenge pages
2. Saves screenshot for debugging
3. Retries with clean browser context
4. Escalates to alert if all retries fail

### Retry Logic
```python
Attempt 1: Scrape → Cloudflare block → Wait 30s
Attempt 2: Scrape → Success → Validate output
Attempt 3: (if needed) Final attempt → Alert if fails
```

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Cloudflare persists | Run with `--max-retries 5` or use proxy |
| Out of memory | Increase EC2 size to t3.medium |
| Timeout | Adjust `PAGE_NAV_WAIT` in scraper.py |
| No data found | Check website structure hasn't changed |

## Monitoring & Alerts

### Slack Integration
```python
# Add to app.py
alert_config = {
    'slack_webhook': os.getenv('SLACK_WEBHOOK_URL')
}
await send_alert("Scraping completed", alert_config)
```

### CloudWatch Metrics (EC2)
```bash
# Install CloudWatch agent
sudo apt-get install amazon-cloudwatch-agent

# Configure to monitor:
# - Scraper success/failure
# - Execution time
# - Output file size
```

## 🧪 Testing

```bash
# Test with single kecamatan (modify scraper.py)
# Run in debug mode
python scraper.py

# Check output validation
python -c "import csv; print(sum(1 for _ in csv.reader(open('harga_medan.csv'))))"
```

## Automated Schedule Options

### Cron (EC2)
```bash
# Daily at 8 AM
0 8 * * * cd /opt/medan-scraper && python app.py

# Every 6 hours
0 */6 * * * cd /opt/medan-scraper && python app.py

# With email on failure
0 8 * * * cd /opt/medan-scraper && python app.py || mail -s "Scraper Failed" admin@example.com
```

### EventBridge (ECS/Lambda)
```json
{
  "ScheduleExpression": "cron(0 8 * * ? *)",
  "State": "ENABLED",
  "Targets": [{
    "Arn": "arn:aws:ecs:region:account:task-definition/medan-scraper",
    "RoleArn": "arn:aws:iam::account:role/ecsEventsRole"
  }]
}
```

## Cost Estimates (AWS)

| Service | Configuration | Monthly Cost |
|---------|--------------|--------------|
| EC2 t3.micro | 1 instance, daily run (5 min) | ~$8.50 |
| ECS Fargate | 0.5 vCPU, 1GB, daily | ~$5.00 |
| Lambda | 1024MB, 5 min/day | ~$0.50 |
| S3 Storage | 1GB (backups) | ~$0.03 |

## Maintenance

### Weekly Tasks
- [ ] Review logs for errors
- [ ] Check output file size (should be >10KB)
- [ ] Verify data freshness

### Monthly Tasks
- [ ] Update Playwright: `playwright install chromium`
- [ ] Rotate logs: `sudo logrotate -f /etc/logrotate.d/medan-scraper`
- [ ] Test website for structural changes

### Update Website Selectors
If the target website changes structure:
```python
# Update these constants in scraper.py
SEL_KECAMATAN = "select[name='id_kecamatan2']"  # New selector
SEL_SEND_BTN = "#sendtbl"                       # New button ID
```

## Contributing

1. Fork the repository
2. Test changes locally with `python app.py --once`
3. Ensure Cloudflare bypass still works
4. Submit PR with description

## License

MIT License - Free for commercial and personal use

## Disclaimer

This tool is for legitimate data collection purposes. Please respect:
- Website terms of service
- Rate limiting (built-in delays)
- Data usage guidelines

<!-- ## Support

- Issues: GitHub Issues
- Documentation: Wiki (link)
- Emergency: Maintainer email -->

## Success Metrics

Typical successful run:
- Duration: 2-5 minutes
- Data rows: 500-1000 per day
- Success rate: >95% (including Cloudflare retries)
- File size: 50-200KB CSV

---

**Built with ❤️ for big big chunk of money from daddy preds** 
<!-- | [Report Issue](link) | [Changelog](CHANGELOG.md) -->
Fuck the goverment, they didn't provide any public api, btw if you try to use the code to scrap, use the manual one, hope it's working fine, godspeed
