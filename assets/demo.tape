# Kodiqa Demo — generates assets/demo.gif
# Install: https://github.com/charmbracelet/vhs
# Run: vhs assets/demo.tape

Output assets/demo.gif

Set FontSize 14
Set Width 1200
Set Height 600
Set Theme "Dracula"
Set TypingSpeed 40ms

Type "kodiqa"
Enter
Sleep 4s

Type "read pyproject.toml and tell me what this project does"
Sleep 500ms
Enter
Sleep 10s

Type "/models"
Enter
Sleep 3s

Type "/stats"
Enter
Sleep 2s

Type "/quit"
Enter
Sleep 1s
