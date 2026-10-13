# Display service

An always-on service for one display:

- **Clock**, set up automatically whenever the display connects, so it comes back by itself after a power cut instead of starting blank.
- **Sleep hours**: the screen turns off and on on a schedule.
- **Morning train board**: live departures from Transport for NSW (the same data TripView uses) during a time window, then back to the clock.

![train board](../images/trainboard.png)

The board shows the line and current time, minutes until the next train (large), the two after it, and whether it's running late. If `WALK_MINUTES` is set, trains you can't reach in time are hidden, and the big number turns amber, then red, when it's time to leave.

It holds a Bluetooth connection open, which is how it notices a power cut straight away. The display only takes one connection at a time, so **stop the service before using the phone app** (`docker compose stop`).

## Setup on a Raspberry Pi

The Pi needs to be within Bluetooth range of the display (roughly one room). Any Pi with built-in Bluetooth works (3B+, 4, Zero 2 W).

1. Install Raspberry Pi OS Lite (64-bit) and Docker:

   ```sh
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER   # log out and back in
   ```

2. Check Bluetooth is up: `bluetoothctl show` should print a controller with `Powered: yes`.

3. Clone this repo, then:

   ```sh
   cp .env.example .env
   docker compose build
   docker compose run --rm idotmatrix scan        # prints the display's address
   ```

   Close the phone app first, otherwise the display is busy and won't show up. Put the address in `IDM_ADDRESS` in `.env`.

4. Get a free API key at <https://opendata.transport.nsw.gov.au>: sign up, create an application and add the **Trip Planner APIs**. Put the key in `TFNSW_API_KEY`, then check the stop and platform:

   ```sh
   docker compose run --rm idotmatrix stops "Central Station"
   docker compose run --rm idotmatrix departures
   ```

   `departures` should list the trains from your platform. If it's empty, run it with `PLATFORM=` in `.env` to see what the platforms are called.

5. Start it:

   ```sh
   docker compose up -d
   docker compose logs -f
   ```

   `restart: unless-stopped` brings it back after the Pi reboots.

## Settings

All in `.env`. See [.env.example](../.env.example) for every option. After changing them, run `docker compose up -d` to apply.

To try a layout without the display, `docker compose run --rm -v $PWD:/out idotmatrix preview /out/preview.png` renders the board using live data if a key is set, otherwise sample trains.

## Troubleshooting

- **`display not reachable` forever**: the phone app is probably connected. Force-close it. Also check the Pi is close enough.
- **D-Bus permission errors**: on hosts with AppArmor (Ubuntu), add `security_opt: [apparmor:unconfined]` to the compose service.
- **Board says `NO DATA`**: the API hasn't answered for 5 minutes. `docker compose logs` shows why (a wrong key gives a 401).
