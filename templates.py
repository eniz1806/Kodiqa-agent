"""Project templates for /init command."""

TEMPLATES = {
    "cli-python": {
        "description": "Python CLI application with argparse and tests",
        "files": {
            "main.py": '#!/usr/bin/env python3\n"""CLI application."""\n\nimport argparse\nimport sys\n\n\ndef main():\n    parser = argparse.ArgumentParser(description="My CLI App")\n    parser.add_argument("name", help="Your name")\n    parser.add_argument("-v", "--verbose", action="store_true")\n    args = parser.parse_args()\n    print(f"Hello, {args.name}!")\n    if args.verbose:\n        print("Verbose mode enabled.")\n\n\nif __name__ == "__main__":\n    main()\n',
            "tests/test_main.py": '"""Tests for main module."""\n\nimport subprocess\nimport sys\n\n\ndef test_help():\n    result = subprocess.run([sys.executable, "main.py", "--help"], capture_output=True, text=True)\n    assert result.returncode == 0\n    assert "CLI App" in result.stdout\n\n\ndef test_basic():\n    result = subprocess.run([sys.executable, "main.py", "World"], capture_output=True, text=True)\n    assert "Hello, World!" in result.stdout\n',
            "pyproject.toml": '[build-system]\nrequires = ["setuptools>=68.0"]\nbuild-backend = "setuptools.build_meta"\n\n[project]\nname = "myapp"\nversion = "0.1.0"\nrequires-python = ">=3.9"\n\n[project.scripts]\nmyapp = "main:main"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "README.md": "# My CLI App\n\n## Install\n```bash\npip install -e .\n```\n\n## Usage\n```bash\nmyapp <name>\n```\n",
            ".gitignore": "__pycache__/\n*.pyc\n*.egg-info/\ndist/\nbuild/\n.venv/\n",
        },
        "commands": [],
    },
    "fastapi": {
        "description": "FastAPI web application with uvicorn",
        "files": {
            "app/main.py": 'from fastapi import FastAPI\n\napp = FastAPI(title="My API")\n\n\n@app.get("/")\ndef root():\n    return {"message": "Hello, World!"}\n\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n',
            "app/__init__.py": "",
            "requirements.txt": "fastapi>=0.100\nuvicorn>=0.22\n",
            "README.md": "# My API\n\n## Install\n```bash\npip install -r requirements.txt\n```\n\n## Run\n```bash\nuvicorn app.main:app --reload\n```\n",
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
        },
        "commands": [],
    },
    "flask": {
        "description": "Flask web application with templates",
        "files": {
            "app.py": 'from flask import Flask, render_template\n\napp = Flask(__name__)\n\n\n@app.route("/")\ndef index():\n    return render_template("index.html", title="Home")\n\n\nif __name__ == "__main__":\n    app.run(debug=True)\n',
            "templates/index.html": '<!DOCTYPE html>\n<html>\n<head><title>{{ title }}</title></head>\n<body>\n  <h1>Hello from Flask!</h1>\n</body>\n</html>\n',
            "requirements.txt": "flask>=3.0\n",
            "README.md": "# My Flask App\n\n## Install\n```bash\npip install -r requirements.txt\n```\n\n## Run\n```bash\npython app.py\n```\n",
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\n",
        },
        "commands": [],
    },
    "react": {
        "description": "React app with Vite and TypeScript",
        "files": {
            "package.json": '{\n  "name": "my-react-app",\n  "private": true,\n  "version": "0.1.0",\n  "type": "module",\n  "scripts": {\n    "dev": "vite",\n    "build": "tsc && vite build",\n    "preview": "vite preview"\n  },\n  "dependencies": {\n    "react": "^18.2.0",\n    "react-dom": "^18.2.0"\n  },\n  "devDependencies": {\n    "@types/react": "^18.2.0",\n    "@types/react-dom": "^18.2.0",\n    "@vitejs/plugin-react": "^4.0.0",\n    "typescript": "^5.0.0",\n    "vite": "^5.0.0"\n  }\n}\n',
            "index.html": '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="UTF-8" />\n  <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n  <title>My React App</title>\n</head>\n<body>\n  <div id="root"></div>\n  <script type="module" src="/src/main.tsx"></script>\n</body>\n</html>\n',
            "src/main.tsx": "import React from 'react'\nimport ReactDOM from 'react-dom/client'\nimport App from './App'\n\nReactDOM.createRoot(document.getElementById('root')!).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>,\n)\n",
            "src/App.tsx": "function App() {\n  return (\n    <div>\n      <h1>Hello React!</h1>\n    </div>\n  )\n}\n\nexport default App\n",
            "tsconfig.json": '{\n  "compilerOptions": {\n    "target": "ES2020",\n    "module": "ESNext",\n    "jsx": "react-jsx",\n    "strict": true,\n    "moduleResolution": "bundler"\n  },\n  "include": ["src"]\n}\n',
            "vite.config.ts": "import { defineConfig } from 'vite'\nimport react from '@vitejs/plugin-react'\n\nexport default defineConfig({\n  plugins: [react()],\n})\n",
            ".gitignore": "node_modules/\ndist/\n",
            "README.md": "# My React App\n\n## Install\n```bash\nnpm install\n```\n\n## Run\n```bash\nnpm run dev\n```\n",
        },
        "commands": [],
    },
    "node-express": {
        "description": "Node.js Express API server",
        "files": {
            "package.json": '{\n  "name": "my-api",\n  "version": "0.1.0",\n  "type": "module",\n  "scripts": {\n    "start": "node index.js",\n    "dev": "node --watch index.js"\n  },\n  "dependencies": {\n    "express": "^4.18.0"\n  }\n}\n',
            "index.js": "import express from 'express'\n\nconst app = express()\nconst PORT = process.env.PORT || 3000\n\napp.use(express.json())\n\napp.get('/', (req, res) => {\n  res.json({ message: 'Hello, World!' })\n})\n\napp.get('/health', (req, res) => {\n  res.json({ status: 'ok' })\n})\n\napp.listen(PORT, () => {\n  console.log(`Server running on http://localhost:${PORT}`)\n})\n",
            ".gitignore": "node_modules/\n",
            "README.md": "# My Express API\n\n## Install\n```bash\nnpm install\n```\n\n## Run\n```bash\nnpm run dev\n```\n",
        },
        "commands": [],
    },
}
