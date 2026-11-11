# OSZ → Monday Morning Misery GitHub Action + built in convert site

Converts an osu!mania 4K chart and extracts the matching song audio.

coverter site https://louaipro99.github.io/MMM-custom-charts/

## Repository layout

```text
your-repository/
├── osz_to_mmm.py
├── charts/
│   └── song.osz
└── .github/
    └── workflows/
        └── convert-osz.yml
```

Commit your `.osz` file to the repository. It must contain the selected `.osu`
beatmap and its audio file referenced by `AudioFilename`.

## Run it

1. Open your repository and select **Actions**.
2. Select **Convert OSZ to MMM Chart + Song**.
3. Click **Run workflow**.
4. Enter the path to your `.osz` file.
5. Choose the difficulty number and target side.
6. Run the workflow.
7. Download the `mmm-chart-and-song` artifact from the completed run.

## Artifact contents

```text
output/
├── chart.json
└── audio/
    └── original-song-file.mp3 (or .ogg, etc.)
```

The audio is extracted from the OSZ using the selected beatmap's `AudioFilename`.
For a standalone `.osu`, its audio file must be beside it at the relative path
specified by `AudioFilename`.

## Notes

- Supports osu!mania 4-key beatmaps.
- Difficulty is a 1-based beatmap number inside the OSZ.
- `player` maps notes to directions 0–3.
- `opponent` maps notes to directions 4–7.
- The workflow uploads the JSON and audio together as one artifact. It does not
  commit generated files back into the repository.
