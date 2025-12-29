#!/bin/bash
# Development server runner

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting Net Stabilization Development Server${NC}"
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
pip install -r requirements.txt -q

# Copy example env if .env doesn't exist
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Creating .env from example...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}Please edit .env with your AwesomeMiner settings${NC}"
fi

# Create data directory if it doesn't exist
mkdir -p data

echo ""
echo -e "${GREEN}Starting server on http://localhost:8080${NC}"
echo -e "Dashboard: ${GREEN}http://localhost:8080/${NC}"
echo -e "API Docs:  ${GREEN}http://localhost:8080/docs${NC}"
echo ""

# Run the application
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
