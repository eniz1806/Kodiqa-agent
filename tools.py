"""Claude native tool definitions for Kodiqa - mirrors Claude Code's tools."""

CLAUDE_TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Always use this before editing a file. Output includes line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to read"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with new content. Creates parent directories if needed. Use read_file first if the file already exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file"
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "edit_file",
        "description": "Edit a file by replacing an exact string match. Always read_file first. The old_string must match exactly (including whitespace and indentation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit"
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find and replace. Must be unique in the file."
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text"
                }
            },
            "required": ["path", "old_string", "new_string"]
        }
    },
    {
        "name": "list_dir",
        "description": "List contents of a directory with file sizes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "tree",
        "description": "Show directory tree structure. Skips .git, node_modules, __pycache__, venv, build, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the root directory"
                },
                "depth": {
                    "type": "integer",
                    "description": "Maximum depth to recurse (default: 3)",
                    "default": 3
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "glob",
        "description": "Find files matching a glob pattern (e.g. '**/*.py', '*.json'). Returns matching file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g. '**/*.py', 'src/**/*.ts')"
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (absolute path)"
                }
            },
            "required": ["pattern", "path"]
        }
    },
    {
        "name": "grep",
        "description": "Search file contents using regex. Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for"
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (absolute path)"
                }
            },
            "required": ["pattern", "path"]
        }
    },
    {
        "name": "run_command",
        "description": "Execute a shell command and return its output. Use for git, npm, python, build tools, etc. Requires user confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "web_search",
        "description": "Search the web using DuckDuckGo. Returns titles, URLs, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_fetch",
        "description": "Fetch and extract readable text from a URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "git_status",
        "description": "Show git status of the current repository.",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "git_diff",
        "description": "Show git diff. Optionally pass args like '--staged' or a file path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "string",
                    "description": "Additional arguments for git diff (e.g. '--staged', 'path/to/file')",
                    "default": ""
                }
            },
        }
    },
    {
        "name": "git_commit",
        "description": "Stage all changes and create a git commit. Requires user confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The commit message"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "memory_store",
        "description": "Store something in persistent memory. Memories survive across sessions. Use for user preferences, project details, important facts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "What to remember"
                },
                "tags": {
                    "type": "string",
                    "description": "Optional tags for categorization",
                    "default": ""
                }
            },
            "required": ["content"]
        }
    },
    {
        "name": "memory_search",
        "description": "Search persistent memory for previously stored information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_image",
        "description": "Read an image file (PNG, JPG, GIF, WebP). Returns the image for visual analysis. Use this when the user asks about screenshots, UI mockups, diagrams, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the image file"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "read_pdf",
        "description": "Extract text from a PDF file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the PDF file"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "ask_user",
        "description": "Ask the user a question to clarify requirements, get preferences, or choose between options. Use this before making assumptions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user. Should be clear and specific."
                },
                "header": {
                    "type": "string",
                    "description": "Short category label (e.g. 'Framework', 'Auth method', 'Approach')"
                },
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {
                                "type": "string",
                                "description": "Short option name (1-5 words)"
                            },
                            "description": {
                                "type": "string",
                                "description": "What this option means or what happens if chosen"
                            }
                        },
                        "required": ["label", "description"]
                    },
                    "description": "2-4 choices for the user to pick from"
                },
                "multi_select": {
                    "type": "boolean",
                    "description": "Allow selecting multiple options (default: false)",
                    "default": False
                }
            },
            "required": ["question"]
        }
    },
]
