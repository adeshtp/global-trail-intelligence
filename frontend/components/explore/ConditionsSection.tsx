"use client";

import { weatherPointLabel } from "@/app/explore/helpers";
import type { TrailIntelligenceResponse, WeatherResponse } from "@/app/explore/types";

type ConditionsSectionProps = {
  loadingWeather: boolean;
  selectedIntelligence: TrailIntelligenceResponse | null;
  weather: WeatherResponse | null;
  weatherError: string | null;
};

export default function ConditionsSection({
  loadingWeather,
  selectedIntelligence,
  weather,
  weatherError,
}: ConditionsSectionProps) {
  return (
    <>
      <section id="conditions" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

        <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">

          <div>

            <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
              Conditions
            </p>


            <h3 className="mt-3 text-[22px] font-semibold">
              Weather & trail conditions
            </h3>

          </div>


          {weather && (
            <p className="text-[11px] text-white/30">
              Live weather at trail midpoint
            </p>
          )}

        </div>


        {/* WEATHER LOADING */}

        {loadingWeather && (
          <div className="mt-6 rounded-2xl border border-white/[0.08] bg-white/[0.025] px-5 py-6">

            <div className="flex items-center gap-3">

              <div className="h-2 w-2 animate-pulse rounded-full bg-emerald-400/70" />

              <p className="text-sm text-white/55">
                Loading current weather…
              </p>

            </div>

          </div>
        )}


        {/* WEATHER ERROR */}

        {!loadingWeather &&
          weatherError && (

            <div className="mt-6 rounded-2xl border border-amber-300/10 bg-amber-300/[0.03] px-5 py-5">

              <p className="text-sm text-white/55">
                {
                  weatherError
                }
              </p>

            </div>
          )}


        {/* WEATHER DATA */}

        {!loadingWeather &&
          !weatherError &&
          weather && (

            <div className="mt-6">

              <div className="rounded-[22px] border border-white/[0.08] bg-gradient-to-br from-white/[0.05] to-white/[0.02] p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">

                <div className="grid gap-5 md:grid-cols-[1.3fr_1fr_1fr]">

                  {/* Temperature */}

                  <div>

                    <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/30">
                      Current
                    </p>


                    <div className="mt-3 flex items-end gap-2">

                      <p className="text-4xl font-semibold tracking-[-0.05em] text-white">

                        {
                          weather.current.temperature !==
                          null
                            ? weather.current.temperature.toFixed(
                                1
                              )
                            : "—"
                        }

                      </p>


                      <span className="mb-1 text-lg text-white/35">
                        °C
                      </span>

                    </div>


                    <p className="mt-2 text-sm text-emerald-300/70">
                      {
                        weather.current.weather_condition
                      }
                    </p>

                  </div>


                  {/* Rain chance */}

                  <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4">

                    <p className="text-[10px] uppercase tracking-[0.16em] text-white/30">
                      Rain chance
                    </p>


                    <p className="mt-3 text-2xl font-semibold tracking-[-0.035em] text-white">

                      {
                        weather.current
                          .precipitation_probability !==
                        null
                          ? `${weather.current.precipitation_probability}%`
                          : "—"
                      }

                    </p>


                    <p className="mt-1 text-[11px] text-white/30">
                      Current hour
                    </p>

                  </div>


                  {/* Precipitation */}

                  <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4">

                    <p className="text-[10px] uppercase tracking-[0.16em] text-white/30">
                      Precipitation
                    </p>


                    <p className="mt-3 text-2xl font-semibold tracking-[-0.035em] text-white">

                      {
                        weather.current.precipitation !==
                        null
                          ? `${weather.current.precipitation.toFixed(
                              1
                            )} mm`
                          : "—"
                      }

                    </p>


                    <p className="mt-1 text-[11px] text-white/30">
                      Current
                    </p>

                  </div>

                </div>


                {/* Secondary weather values */}

                <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">

                  {/* Humidity */}

                  <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">

                    <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                      Humidity
                    </p>


                    <p className="mt-2 text-sm font-semibold text-white/80">

                      {
                        weather.current.humidity !==
                        null
                          ? `${weather.current.humidity}%`
                          : "—"
                      }

                    </p>

                  </div>


                  {/* Wind */}

                  <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">

                    <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                      Wind
                    </p>


                    <p className="mt-2 text-sm font-semibold text-white/80">

                      {
                        weather.current.wind_speed !==
                        null
                          ? `${weather.current.wind_speed.toFixed(
                              1
                            )} km/h`
                          : "—"
                      }

                    </p>

                  </div>


                  {/* Rain */}

                  <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">

                    <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                      Rain
                    </p>


                    <p className="mt-2 text-sm font-semibold text-white/80">

                      {
                        weather.current.rain !==
                        null
                          ? `${weather.current.rain.toFixed(
                              1
                            )} mm`
                          : "—"
                      }

                    </p>

                  </div>


                  {/* Updated */}

                  <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">

                    <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                      Updated
                    </p>


                    <p className="mt-2 text-sm font-semibold text-white/80">

                      {
                        weather.current.time
                          ? weather.current.time.slice(
                              11,
                              16
                            )
                          : "—"
                      }

                    </p>


                    <p className="text-[10px] text-white/25">
                      Local time
                    </p>

                  </div>

                </div>

              </div>


              {(weather.recent_rain || weather.recent_precipitation) && (
                <div className="mt-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] px-4 py-4">
                  <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                    Recent rainfall
                  </p>
                  <div className="mt-3 grid grid-cols-3 gap-3 text-center">
                    <div>
                      <p className="text-sm font-semibold text-white/80">
                        {weather.recent_rain?.["24h_mm"] != null
                          ? `${weather.recent_rain["24h_mm"]!.toFixed(1)} mm`
                          : weather.recent_precipitation?.["24h_mm"] != null
                            ? `${weather.recent_precipitation?.["24h_mm"]!.toFixed(1)} mm`
                            : "—"}
                      </p>
                      <p className="mt-1 text-[10px] text-white/25">24h</p>
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-white/80">
                        {weather.recent_rain?.["48h_mm"] != null
                          ? `${weather.recent_rain["48h_mm"]!.toFixed(1)} mm`
                          : weather.recent_precipitation?.["48h_mm"] != null
                            ? `${weather.recent_precipitation?.["48h_mm"]!.toFixed(1)} mm`
                            : "—"}
                      </p>
                      <p className="mt-1 text-[10px] text-white/25">48h</p>
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-white/80">
                        {weather.recent_rain?.["72h_mm"] != null
                          ? `${weather.recent_rain["72h_mm"]!.toFixed(1)} mm`
                          : weather.recent_precipitation?.["72h_mm"] != null
                            ? `${weather.recent_precipitation?.["72h_mm"]!.toFixed(1)} mm`
                            : "—"}
                      </p>
                      <p className="mt-1 text-[10px] text-white/25">72h</p>
                    </div>
                  </div>
                </div>
              )}


              {weather.aggregation === "worst_case" &&
              weather.samples &&
              weather.samples.length > 1 ? (
                <div className="mt-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5">
                  <p className="text-[10px] leading-5 text-white/40">
                    The figures above are the harshest of{" "}
                    {weather.samples.length} points along the route, each
                    read at its own elevation.
                  </p>
                  <ul className="mt-1.5 space-y-0.5">
                    {weather.samples.map((sample) => (
                      <li
                        key={`${sample.latitude}-${sample.longitude}-${sample.elevation_m}`}
                        className="text-[11px] leading-5 text-white/50"
                      >
                        <span className="capitalize text-white/70">
                          {sample.labels.join(" / ")}
                        </span>
                        {`, ${Math.round(sample.elevation_m)} m: `}
                        {sample.temperature !== null
                          ? `${sample.temperature.toFixed(1)} °C`
                          : "—"}
                        {sample.wind_speed !== null
                          ? `, wind ${sample.wind_speed.toFixed(0)} km/h`
                          : ""}
                        {sample.weather_condition
                          ? `, ${sample.weather_condition.toLowerCase()}`
                          : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <p className="mt-3 text-[10px] leading-5 text-white/25">
                Weather source: {weather.source}
                {weather.aggregation === "worst_case"
                  ? " · read along the selected route"
                  : ` · measured at ${weatherPointLabel(
                      selectedIntelligence
                        ? selectedIntelligence.weather_coordinate
                        : undefined,
                    )}`}
                {" — on the selected route, not the place you searched."}
              </p>

            </div>
          )}

      </section>
    </>
  );
}
