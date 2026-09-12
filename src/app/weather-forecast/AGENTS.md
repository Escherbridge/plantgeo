# Forecast review page

`/weather-forecast` is an isolated local review surface for the governed Parquet
fixture, not an admitted live forecast. It does not change observation mode,
the map's time slider, shared layer registry or WeatherDetails ownership.

The form explicitly chooses coordinates, run and first valid UTC hour. Submit
clears prior values and pins the whole returned series. The range input, hour
buttons and text table expose the same valid hours to keyboard and screen-reader
users. UTC is deliberate until a separately tested local-day/DST contract is
admitted. The page uses a scrollable main region because the application body
disables overflow. All interactive controls have 44px minimum targets.

Real-data visual, scientific and mobile performance acceptance remains blocked
on source admission and shared integration ownership transfers. This page makes
no continuous-field or ensemble uncertainty claim.
