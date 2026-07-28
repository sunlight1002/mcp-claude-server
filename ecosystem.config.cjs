module.exports = {
  apps: [
    {
      name: "mcp-claude",
      script: "server.py",
      interpreter: "/home/ubuntu/mcp-claude-server/venv/bin/python",
      cwd: "/home/ubuntu/mcp-claude-server",
      // Secrets/credentials come from .env via python-dotenv in server.py.
      env: {
        MCP_HOST: "127.0.0.1",
        MCP_PORT: "8000",
        MCP_DOMAIN: "mcp.claude.lee-associates-southflorida.com",
        PARCELSCRAPER_API_URL: "https://automation.lee-associates-southflorida.com",
        ADMINSITE_API_URL: "https://admin.lee-associates-southflorida.com",
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
    },
  ],
};
