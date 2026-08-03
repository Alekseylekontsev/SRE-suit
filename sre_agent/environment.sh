#!/bin/bash
# =============================================================================
# Environment Setup Script for SRE Agent
# =============================================================================
# Copy this file to .env and customize values for your environment
# Usage: source environment.sh

# =============================================================================
# PROJECT CONFIGURATION
# =============================================================================
export SRE_PROJECT_ID="1234"
export SRE_PROJECT_NAME="Project_1"
export SRE_SYSTEM_NAME="DC6"

# =============================================================================
# ENVIRONMENT
# =============================================================================
export SRE_ENV="development"  # production, staging, development
export SRE_CONFIG_PATH=""      # Path to config file (optional)

# =============================================================================
# PROXMOX CONFIGURATION
# =============================================================================
# Format: host:port or just host
export PROXMOX_HOST="https://192.0.2.10:8006"
export PROXMOX_TOKEN="root@pam!sre-agent=<YOUR_TOKEN>"

# Multiple nodes (comma-separated or use array in config)
export PROXMOX_NODES_0_HOST="https://192.0.2.10:8006"
export PROXMOX_NODES_0_TOKEN="root@pam!sre-agent=<TOKEN1>"
export PROXMOX_NODES_1_HOST="https://192.0.2.11:8006"
export PROXMOX_NODES_1_TOKEN="root@pam!sre-agent=<TOKEN2>"

# =============================================================================
# HETZNER CLOUD
# =============================================================================
export HETZNER_TOKEN="<HETZNER_API_TOKEN>"

# =============================================================================
# APPROVAL CHANNELS
# =============================================================================
# Primary: slack, gmail, both
export SRE_APPROVAL_CHANNEL="slack"

# Slack
export SLACK_WEBHOOK="<SLACK_WEBHOOK_URL>"
export SLACK_CHANNEL="#sre-approvals"

# Gmail / Google Group
export GMAIL_EMAIL="sre-automation@example.com"
export GMAIL_PASSWORD="<APP_PASSWORD>"
export GMAIL_GROUP="sre-approvals@example.com"

# =============================================================================
# OBSERVABILITY
# =============================================================================
export SRE_METRICS_BACKEND="prometheus"
export PROMETHEUS_URL="http://prometheus:9090"

# Zabbix (if used)
export ZABBIX_URL="http://zabbix.example.com/api_jsonrpc.php"
export ZABBIX_TOKEN="<ZABBIX_TOKEN>"

# =============================================================================
# GITLAB TERRAFORM STATE
# =============================================================================
export GITLAB_URL="https://gitlab.example.com"
export GITLAB_PROJECT="admins/hetzner_proxmox"
export GITLAB_TOKEN="<GITLAB_TOKEN>"

# =============================================================================
# NETWORK CONFIGURATION
# =============================================================================
export SRE_EXTERNAL_IP="192.0.2.10"

# =============================================================================
# BATCH APPROVAL SETTINGS
# =============================================================================
export SRE_BATCH_WINDOW="180"  # minutes

echo "SRE Agent environment loaded: $SRE_ENV"
echo "Project: $SRE_PROJECT_ID $SRE_PROJECT_NAME $SRE_SYSTEM_NAME"
