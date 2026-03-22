export interface WeatherInput {
  temp: number;
  rh: number;
  wind: number;
  rain: number;
  month: number;
}

export interface FWIState {
  ffmc: number;
  dmc: number;
  dc: number;
}

export interface FWIResult {
  ffmc: number;
  dmc: number;
  dc: number;
  isi: number;
  bui: number;
  fwi: number;
}

// Van Wagner 1987 standard FWI System equations

export function calculateFFMC(
  temp: number,
  rh: number,
  wind: number,
  rain: number,
  prevFFMC: number
): number {
  let mo = 147.2 * (101 - prevFFMC) / (59.5 + prevFFMC);

  if (rain > 0.5) {
    const rf = rain - 0.5;
    if (mo > 150) {
      mo =
        mo +
        42.5 * rf * Math.exp(-100 / (251 - mo)) * (1 - Math.exp(-6.93 / rf)) +
        0.0015 * Math.pow(mo - 150, 2) * Math.pow(rf, 0.5);
    } else {
      mo =
        mo +
        42.5 * rf * Math.exp(-100 / (251 - mo)) * (1 - Math.exp(-6.93 / rf));
    }
    if (mo > 250) mo = 250;
  }

  const ed =
    0.942 * Math.pow(rh, 0.679) +
    11 * Math.exp((rh - 100) / 10) +
    0.18 * (21.1 - temp) * (1 - Math.exp(-0.115 * rh));

  const ew =
    0.618 * Math.pow(rh, 0.753) +
    10 * Math.exp((rh - 100) / 10) +
    0.18 * (21.1 - temp) * (1 - Math.exp(-0.115 * rh));

  let m: number;
  if (mo > ed) {
    const ko =
      0.424 * (1 - Math.pow(rh / 100, 1.7)) +
      0.0694 * Math.pow(wind, 0.5) * (1 - Math.pow(rh / 100, 8));
    const kd = ko * 0.581 * Math.exp(0.0365 * temp);
    m = ed + (mo - ed) * Math.pow(10, -kd);
  } else if (mo < ew) {
    const kl =
      0.424 * (1 - Math.pow((100 - rh) / 100, 1.7)) +
      0.0694 * Math.pow(wind, 0.5) * (1 - Math.pow((100 - rh) / 100, 8));
    const kw = kl * 0.581 * Math.exp(0.0365 * temp);
    m = ew - (ew - mo) * Math.pow(10, -kw);
  } else {
    m = mo;
  }

  return Math.round((59.5 * (250 - m) / (147.2 + m)) * 10) / 10;
}

// Month-based day-length adjustment factors for DMC (Van Wagner 1987, Table 1)
const DMC_DAY_LENGTH = [6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0];

export function calculateDMC(
  temp: number,
  rh: number,
  rain: number,
  prevDMC: number,
  month: number
): number {
  let re: number;
  let mo: number;
  let b: number;
  let pr: number;

  if (rain > 1.5) {
    re = 0.92 * rain - 1.27;
    mo = 20 + Math.exp(5.6348 - prevDMC / 43.43);

    if (prevDMC <= 33) {
      b = 100 / (0.5 + 0.3 * prevDMC);
    } else if (prevDMC <= 65) {
      b = 14 - 1.3 * Math.log(prevDMC);
    } else {
      b = 6.2 * Math.log(prevDMC) - 17.2;
    }

    const mr = mo + 1000 * re / (48.77 + b * re);
    pr = 244.72 - 43.43 * Math.log(mr - 20);
    pr = Math.max(0, pr);
  } else {
    pr = prevDMC;
  }

  if (temp < -1.1) return pr;

  const k =
    1.894 * (temp + 1.1) * (100 - rh) * DMC_DAY_LENGTH[(month - 1) % 12] * 1e-6;
  return Math.round((pr + 100 * k) * 10) / 10;
}

// Month-based day-length factor for DC (Van Wagner 1987, Table 2)
const DC_DAY_LENGTH = [-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6];

export function calculateDC(
  temp: number,
  rain: number,
  prevDC: number,
  month: number
): number {
  let dr: number;

  if (rain > 2.8) {
    const rd = 0.83 * rain - 1.27;
    const qo = 800 * Math.exp(-prevDC / 400);
    const qr = qo + 3.937 * rd;
    dr = 400 * Math.log(800 / qr);
    dr = Math.max(0, dr);
  } else {
    dr = prevDC;
  }

  const lf = DC_DAY_LENGTH[(month - 1) % 12];
  const v = 0.36 * (temp + 2.8) + lf;

  return Math.round((dr + 0.5 * Math.max(0, v)) * 10) / 10;
}

export function calculateISI(wind: number, ffmc: number): number {
  const fm = 147.2 * (101 - ffmc) / (59.5 + ffmc);
  const ff = 19.115 * Math.exp(-0.1386 * fm) * (1 + Math.pow(fm, 5.31) / 49300000);
  return Math.round(0.208 * ff * Math.exp(0.05039 * wind) * 10) / 10;
}

export function calculateBUI(dmc: number, dc: number): number {
  let bui: number;
  if (dmc <= 0.4 * dc) {
    bui = 0.8 * dmc * dc / (dmc + 0.4 * dc);
  } else {
    bui = dmc - (1 - 0.8 * dc / (dmc + 0.4 * dc)) * (0.92 + Math.pow(0.0114 * dmc, 1.7));
  }
  return Math.round(Math.max(0, bui) * 10) / 10;
}

export function calculateFWI(isi: number, bui: number): number {
  let bb: number;
  if (bui <= 80) {
    bb = 0.1 * isi * (0.626 * Math.pow(bui, 0.809) + 2);
  } else {
    bb = 0.1 * isi * (1000 / (25 + 108.64 * Math.exp(-0.023 * bui)));
  }

  let fwi: number;
  if (bb <= 1) {
    fwi = bb;
  } else {
    fwi = Math.exp(2.72 * Math.pow(0.434 * Math.log(bb), 0.647));
  }
  return Math.round(fwi * 10) / 10;
}

export function calculateFullFWI(weather: WeatherInput, prev: FWIState): FWIResult {
  const ffmc = calculateFFMC(weather.temp, weather.rh, weather.wind, weather.rain, prev.ffmc);
  const dmc = calculateDMC(weather.temp, weather.rh, weather.rain, prev.dmc, weather.month);
  const dc = calculateDC(weather.temp, weather.rain, prev.dc, weather.month);
  const isi = calculateISI(weather.wind, ffmc);
  const bui = calculateBUI(dmc, dc);
  const fwi = calculateFWI(isi, bui);

  return { ffmc, dmc, dc, isi, bui, fwi };
}
