import { Component, OnInit } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute } from '@angular/router';
import { CommonModule } from '@angular/common';
import { ChangeDetectorRef } from '@angular/core';

interface Playlist {
  id: string;
  name: string;
  expanded?: boolean;
  tracks?: Track[];
}

interface Track {
  name: string;
  artist: string;
  downloaded?: boolean;
  downloading?: boolean;
}

@Component({
  standalone: true,
  selector: 'app-home',
  templateUrl: './home.component.html',
  styleUrls: ['./home.component.css'],
  imports: [CommonModule],
})
export class HomeComponent implements OnInit {
  playlists: Playlist[] = [];

  progress = {
    active: false,
    currentSong: '',
    currentIndex: 0,
    total: 0,
    percent: 0,
  };

  constructor(
    private http: HttpClient,
    private route: ActivatedRoute,
    private cdr: ChangeDetectorRef
  ) { }

  ngOnInit() {
    this.route.queryParams.subscribe((params) => {
      if (params['login'] === 'success') {
        console.log('%c✅ Login successful!', 'color: green');
      } else if (params['login'] === 'fail') {
        console.error('❌ Login failed!');
      }
    });

    this.http.get<Playlist[]>('http://localhost:8000/playlists').subscribe(
      (data) => {
        this.playlists = data;
      },
      (error) => {
        console.error('Failed to fetch playlists:', error);
      }
    );
  }

  toggle(playlist: Playlist) {
    playlist.expanded = !playlist.expanded;

    if (playlist.expanded && !playlist.tracks) {
      this.http
        .get<Track[]>(`http://localhost:8000/playlists/${playlist.id}/tracks`)
        .subscribe(
          (tracks) => {
            playlist.tracks = tracks;
          },
          (error) => {
            console.error('Failed to fetch tracks:', error);
          }
        );
    }
  }

  download(track: Track) {
    track.downloading = true;

    const query = `${track.name} Lyrics`;
    const author = track.artist;

    // Step 1: Search YouTube
    this.http
      .get<{ title: string; url: string }>(
        'http://localhost:8000/youtube/search',
        { params: { query: query, author: author } }
      )
      .subscribe(
        (res) => {
          if (!res.url || res.url === 'No video found') {
            console.error('YouTube video not found');
            track.downloading = false;
            return;
          }

          const filename = `${track.name} by ${track.artist}`;
          const payload = {
            url: res.url,
            filename: filename,
            author: track.artist,      // artist metadata
            album: undefined          // optional, set if you have an album value
          };

          // Step 2: Request download
          this.http
            .post('http://localhost:8000/youtube/download-audio', payload, {
              responseType: 'blob'
            })
            .subscribe(
              (blob) => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${filename}.mp3`;
                a.click();
                window.URL.revokeObjectURL(url);
                track.downloading = false;
              },
              (err) => {
                console.error('Error downloading audio:', err);
                track.downloading = false;
              }
            );
        },
        (err) => {
          console.error('YouTube search error:', err);
          track.downloading = false;
        }
      );
  }

  downloadPlaylist(playlist: { id: string; name: string }) {
    if (!confirm(`Download all songs from "${playlist.name}"?`)) return;

    this.progress.active = true;
    this.progress.currentSong = '';
    this.progress.currentIndex = 0;
    this.progress.total = 0;
    this.progress.percent = 0;

    const eventSource = new EventSource(
      `http://localhost:8000/playlists/${playlist.id}/download-all-stream`
    );

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.error) {
        alert(data.error);
        this.progress.active = false;
        eventSource.close();
        return;
      }

      if (data.done) {
        this.progress.active = false;
        this.progress.currentSong = '';
        this.progress.currentIndex = 0;
        this.progress.total = 0;
        this.progress.percent = 0;

        this.cdr.detectChanges(); // force progress bar to disappear
        alert(`✅ Finished downloading "${playlist.name}"!`);
        eventSource.close();

        // Refresh checkboxes
        this.http
          .get<Track[]>(`http://localhost:8000/playlists/${playlist.id}/tracks`)
          .subscribe((tracks) => {
            const pl = this.playlists.find((p) => p.id === playlist.id);
            if (pl) pl.tracks = tracks;
          });

        return;
      }

      // ✅ Set progress bar values first
      if (data.total && data.index) {
        const percent = (data.index / data.total) * 100;
        this.progress.percent = isFinite(percent) ? Math.round(percent) : 0;
        this.progress.currentIndex = data.index;
        this.progress.total = data.total;
        this.progress.currentSong = data.song; // ✅ safe now
      }

      this.cdr.detectChanges(); // ✅ Force UI update
      console.log(`🎧 ${data.status.toUpperCase()}: ${data.song} (${data.duration}s)`);
    };

    eventSource.onerror = (err) => {
      console.error('SSE error:', err);
      this.progress.active = false;
      eventSource.close();
    };
  }
}
