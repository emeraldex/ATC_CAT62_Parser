#!/bin/bash
# Installation script for CAT62 Parser on Linux/macOS

set -e

echo "================================"
echo "CAT62 Parser Installation"
echo "================================"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check Python version
echo -e "${BLUE}Checking Python version...${NC}"
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $PYTHON_VERSION"

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>/dev/null; then
    echo "Error: Python 3.8 or higher required"
    exit 1
fi

# Create virtual environment
echo -e "${BLUE}Creating virtual environment...${NC}"
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
echo -e "${BLUE}Upgrading pip...${NC}"
pip install --upgrade pip setuptools wheel

# Install dependencies
echo -e "${BLUE}Installing dependencies...${NC}"
pip install websockets

# Optional: Install test dependencies
echo -e "${BLUE}Installing test dependencies...${NC}"
pip install pytest 2>/dev/null || echo "Warning: pytest not installed"

# Create client directory
mkdir -p client

# Verify installation
echo -e "${BLUE}Verifying installation...${NC}"
python parser_server.py --help > /dev/null 2>&1 && echo -e "${GREEN}✓ Parser executable${NC}" || echo "✗ Parser not working"

echo ""
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Installation complete!${NC}"
echo -e "${GREEN}================================${NC}"
echo ""
echo "Next steps:"
echo "1. Activate environment: source venv/bin/activate"
echo "2. Run parser: python parser_server.py --udp 0.0.0.0:31002"
echo "3. Open browser: http://localhost:7878"
echo ""
echo "For systemd service:"
echo "  sudo cp cat62-parser.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable cat62-parser"
echo "  sudo systemctl start cat62-parser"
echo ""
echo "For Docker:"
echo "  docker-compose up -d"
echo ""
