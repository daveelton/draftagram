#include "ToneControl.h"

#include <cmath>
#include <cstdio>
#include <vector>

/** Plays a 5 kHz tone through the tone control at three settings and prints how loud it comes out. */
int main()
{
    const double sampleRate = 48000.0;
    const double pi = 3.14159265358979323846;

    for (double tone : { 0.0, 0.5, 1.0 })
    {
        ToneControl control;
        control.prepare(sampleRate);
        control.setTone(tone);

        std::vector<double> samples(4800);
        for (size_t i = 0; i < samples.size(); ++i)
            samples[i] = std::sin(2.0 * pi * 5000.0 * double(i) / sampleRate);

        control.process(samples.data(), int(samples.size()));

        double peak = 0.0;
        for (size_t i = samples.size() / 2; i < samples.size(); ++i)
            peak = std::max(peak, std::abs(samples[i]));

        std::printf("tone %.1f: a 5 kHz tone comes out at %.3f\n", tone, peak);
    }
    return 0;
}
