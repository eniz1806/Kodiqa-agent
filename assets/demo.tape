# Kodiqa Demo — generates assets/demo.gif
# Install: https://github.com/charmbracelet/vhs
# Run: vhs assets/demo.tape
#
# NOTE: This records a live terminal session. kodiqa must be installed
# and either Ollama running or an API key configured.
# For best results, ensure a fast model is available (e.g. llama3.2:1b).

Output assets/demo.gif

Set FontSize 16
Set Width 1400
Set Height 650
Set Theme "Dracula"
Set TypingSpeed 50ms
Set Padding 20

# Show help first (instant, no AI needed)
Type "kodiqa --help"
Enter
Sleep 8s

# Launch kodiqa
Type "kodiqa"
Enter
Sleep 12s

Type "skip"
Enter
Sleep 8s

# Show available slash commands
Type "/help"
Enter
Sleep 8s

# Show available models
Type "/models"
Enter
Sleep 8s

# Pick an API model
Type "1"
Enter
Sleep 8s

# Chat with the model
Type "You there?"
Enter
Sleep 8s

# Show session stats
Type "/stats"
Enter
Sleep 8s

# Show token usage
Type "/tokens"
Enter
Sleep 8s

# Exit
Type "/quit"
Enter
Sleep 2s
